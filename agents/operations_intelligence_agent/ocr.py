from __future__ import annotations

import csv
import json
import logging
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .models import METRIC_FIELDS, MetricValue, ExtractedReport

LOGGER = logging.getLogger(__name__)

LABELS: dict[str, tuple[str, ...]] = {
    "accounts_worked": ("accounts worked", "accounts", "acct worked", "worked"),
    "attempts": ("attempts", "attempt"),
    "rpc": ("rpc", "right party contacts"),
    "contact_rate": ("contact rate", "contact %"),
    "close_rate": ("close rate", "close %"),
    "dollars_per_contact": ("dollars per contact", "$ per contact", "dpc"),
    "activated": ("activated",),
    "moved_to_hot": ("moved to hot", "hot"),
    "live_contacts": ("live message/live contacts", "live contacts", "live message", "live contact"),
    "no_answer": ("no answer",),
    "left_message": ("left message",),
    "average_agent": ("average/agent", "average agent", "avg/agent", "avg agent"),
    "emails_sent": ("emails sent",),
    "campaigns": ("campaigns",),
    "unique_open": ("unique open",),
    "unique_link": ("unique link",),
    "open_rate": ("open rate", "open %"),
    "pay_rate": ("pay rate", "pay %"),
    "posted_cash": ("posted cash",),
    "posted_fees": ("posted fees",),
    "pending_cash": ("pending cash",),
    "pending_fees": ("pending fees",),
    "green_cleared_cash": ("green/cleared cash", "green cleared cash", "cleared cash"),
    "future_scheduled_cash": ("future scheduled cash", "future cash", "scheduled cash"),
    "future_scheduled_fees": ("future scheduled fees", "future fees", "scheduled fees"),
}

MONEY_FIELDS = {
    "dollars_per_contact",
    "posted_cash",
    "posted_fees",
    "pending_cash",
    "pending_fees",
    "green_cleared_cash",
    "future_scheduled_cash",
    "future_scheduled_fees",
}
PERCENT_FIELDS = {"contact_rate", "close_rate", "open_rate", "pay_rate"}
NUMBER_RE = re.compile(r"\$?\(?-?\d[\d,]*(?:\.\d+)?\)?%?")
COUNT_FIELDS = set(METRIC_FIELDS) - MONEY_FIELDS - PERCENT_FIELDS
REGION_CONFIG_PATH = Path(__file__).with_name("region_config.json")


@dataclass(frozen=True)
class OcrRegion:
    name: str
    path: Path
    coordinates: tuple[int, int, int, int]
    relative: dict[str, float]
    text: str
    confidence: float
    parsed_metrics: dict[str, MetricValue] = field(default_factory=dict)


class ScreenshotOcrExtractor:
    def __init__(
        self,
        ocr_command: str = "tesseract",
        min_confidence: float = 0.72,
        collector_codes: tuple[str, ...] = (),
        debug_enabled: bool = False,
        debug_root: Path | None = None,
    ) -> None:
        self.ocr_command = ocr_command
        self.min_confidence = min_confidence
        self.collector_codes = collector_codes
        self.debug_enabled = debug_enabled
        self.debug_root = debug_root

    def extract(
        self,
        image_path: Path,
        report_date: str,
        screenshot_hash: str,
        debug_dir: Path | None = None,
    ) -> ExtractedReport:
        with tempfile.TemporaryDirectory() as temp_dir:
            regions, image_size = self._run_region_ocr(image_path, Path(temp_dir))
            ocr_text = "\n\n".join(f"=== {region.name} ===\n{region.text}" for region in regions if region.text.strip())
            confidences = [region.confidence for region in regions if region.confidence > 0]
            word_confidence = sum(confidences) / len(confidences) if confidences else 0.0
            metrics: dict[str, MetricValue] = {
                field: MetricValue(value=None, raw_text="", confidence=0.0)
                for field in METRIC_FIELDS
            }
            missing_fields: list[str] = []
            review_notes: list[str] = []

            if not ocr_text.strip():
                review_notes.append("No OCR text was extracted from the screenshot.")

            for region in regions:
                self._apply_region_metrics(metrics, region)

            normalized_lines = [line.strip() for line in ocr_text.splitlines() if line.strip()]
            self._apply_portfolio_table_fallback(metrics, normalized_lines, word_confidence)
            for field in METRIC_FIELDS:
                if metrics[field].value is not None:
                    continue
                metrics[field] = self._extract_metric(field, normalized_lines, word_confidence)
            self._apply_derived_metrics(metrics)

            for field in METRIC_FIELDS:
                metric = metrics[field]
                if metric.value is None:
                    missing_fields.append(field)
                elif metric.confidence < self.min_confidence:
                    review_notes.append(f"{field} confidence is low ({metric.confidence:.0%}).")

            collector_totals = self._extract_collector_totals(normalized_lines)
            if not collector_totals:
                review_notes.append("Collector/agent totals were not readable from the whiteboard section.")

            report = ExtractedReport(
                report_date=report_date,
                screenshot_hash=screenshot_hash,
                screenshot_path=image_path,
                ocr_text=ocr_text,
                metrics=metrics,
                collector_totals=collector_totals,
                missing_fields=missing_fields,
                manual_review_notes=review_notes,
            )

            resolved_debug_dir = debug_dir
            if resolved_debug_dir is None and self.debug_enabled and self.debug_root:
                resolved_debug_dir = self.debug_root / report_date / screenshot_hash[:12]
            if resolved_debug_dir:
                self._write_debug_artifacts(image_path, resolved_debug_dir, regions, image_size, report)
            return report

    def _run_region_ocr(self, image_path: Path, temp_dir: Path) -> tuple[list[OcrRegion], tuple[int, int]]:
        if not shutil.which(self.ocr_command):
            LOGGER.warning("OCR command %s was not found", self.ocr_command)
            return [], (0, 0)

        try:
            image_size = self._image_size(image_path)
            region_images = self._region_images(image_path, temp_dir, image_size)
            regions: list[OcrRegion] = []
            for label, path, coordinates, relative in region_images:
                text, confidence = self._run_tesseract_best(path, temp_dir / f"ocr-{label}", label)
                region = OcrRegion(
                    name=label,
                    path=path,
                    coordinates=coordinates,
                    relative=relative,
                    text=text,
                    confidence=confidence,
                )
                regions.append(region)
            return regions, image_size
        except (subprocess.SubprocessError, OSError) as exc:
            LOGGER.warning("OCR failed for %s: %s", image_path, exc)
            return [], (0, 0)

    def _run_tesseract_best(self, image_path: Path, output_base: Path, region_name: str) -> tuple[str, float]:
        modes = ("6", "11", "12") if region_name != "whiteboard" else ("6", "11")
        best_text = ""
        best_confidence = -1.0
        results: list[tuple[str, float]] = []
        for mode in modes:
            text, confidence = self._run_tesseract(image_path, output_base.with_name(f"{output_base.name}-psm{mode}"), mode)
            results.append((text, confidence))
            if confidence > best_confidence or (confidence == best_confidence and len(text) > len(best_text)):
                best_text = text
                best_confidence = confidence
        if region_name in {"overview_cards", "activity_section", "performance_section"}:
            numeric_text, numeric_confidence = max(results, key=lambda item: len(NUMBER_RE.findall(item[0])))
            if len(NUMBER_RE.findall(numeric_text)) > len(NUMBER_RE.findall(best_text)):
                best_text = f"{best_text}\n{numeric_text}".strip()
                best_confidence = max(best_confidence, numeric_confidence)
        return best_text, max(best_confidence, 0.0)

    def _run_tesseract(self, image_path: Path, output_base: Path, page_segmentation_mode: str) -> tuple[str, float]:
        text_cmd = [self.ocr_command, str(image_path), str(output_base), "--psm", page_segmentation_mode]
        data_cmd = [self.ocr_command, str(image_path), "stdout", "--psm", page_segmentation_mode, "tsv"]
        subprocess.run(text_cmd, check=True, capture_output=True, text=True, timeout=60)
        text = output_base.with_suffix(".txt").read_text(errors="ignore")
        data = subprocess.run(data_cmd, check=True, capture_output=True, text=True, timeout=60)
        return text, self._average_confidence(data.stdout)

    def _region_images(
        self,
        image_path: Path,
        temp_dir: Path,
        image_size: tuple[int, int],
    ) -> list[tuple[str, Path, tuple[int, int, int, int], dict[str, float]]]:
        width, height = image_size
        regions: list[tuple[str, Path, tuple[int, int, int, int], dict[str, float]]] = []
        full_path = temp_dir / "full_image_preprocessed.png"
        self._preprocess_image(image_path, full_path, scale=2)
        regions.append(("full_image", full_path, (0, 0, width, height), {"x": 0, "y": 0, "w": 1, "h": 1}))
        region_config = self._load_region_config()
        for label, relative in region_config.items():
            box = self._relative_box(relative, image_size)
            crop_path = temp_dir / f"{label}_crop.png"
            self._crop_and_preprocess(image_path, crop_path, box, scale=3)
            regions.append((label, crop_path, box, relative))
        return regions

    def _load_region_config(self) -> dict[str, dict[str, float]]:
        if REGION_CONFIG_PATH.exists():
            return json.loads(REGION_CONFIG_PATH.read_text())
        return {}

    def _image_size(self, image_path: Path) -> tuple[int, int]:
        try:
            from PIL import Image
        except ImportError:
            output = subprocess.run(
                ["sips", "-g", "pixelWidth", "-g", "pixelHeight", str(image_path)],
                check=True,
                capture_output=True,
                text=True,
            ).stdout
            width = int(re.search(r"pixelWidth:\s*(\d+)", output).group(1))
            height = int(re.search(r"pixelHeight:\s*(\d+)", output).group(1))
            return width, height
        with Image.open(image_path) as image:
            return image.size

    def _relative_box(self, relative: dict[str, float], image_size: tuple[int, int]) -> tuple[int, int, int, int]:
        width, height = image_size
        x = int(width * relative["x"])
        y = int(height * relative["y"])
        w = int(width * relative["w"])
        h = int(height * relative["h"])
        return x, y, w, h

    def _crop_and_preprocess(self, source: Path, target: Path, box: tuple[int, int, int, int], scale: int) -> None:
        x, y, width, height = box
        try:
            from PIL import Image, ImageEnhance, ImageFilter
        except ImportError:
            subprocess.run(
                ["sips", "-c", str(height), str(width), "--cropOffset", str(y), str(x), str(source), "--out", str(target)],
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                ["sips", "-Z", str(max(width, height) * scale), str(target)],
                check=True,
                capture_output=True,
                text=True,
            )
            return

        with Image.open(source) as image:
            crop = image.crop((x, y, x + width, y + height))
            self._preprocess_pillow_image(crop, scale).save(target)

    def _preprocess_image(self, source: Path, target: Path, scale: int) -> None:
        try:
            from PIL import Image
        except ImportError:
            shutil.copyfile(source, target)
            subprocess.run(
                ["sips", "-Z", "2600", str(target)],
                check=True,
                capture_output=True,
                text=True,
            )
            return
        with Image.open(source) as image:
            self._preprocess_pillow_image(image, scale).save(target)

    def _preprocess_pillow_image(self, image: Any, scale: int):
        from PIL import ImageEnhance, ImageFilter

        converted = image.convert("L")
        resized = converted.resize((converted.width * scale, converted.height * scale))
        contrasted = ImageEnhance.Contrast(resized).enhance(1.8)
        sharpened = contrasted.filter(ImageFilter.SHARPEN)
        return sharpened.point(lambda pixel: 255 if pixel > 175 else 0)

    def _average_confidence(self, tsv_text: str) -> float:
        scores: list[float] = []
        reader = csv.DictReader(tsv_text.splitlines(), delimiter="\t")
        for row in reader:
            text = (row.get("text") or "").strip()
            if not text:
                continue
            try:
                confidence = float(row.get("conf", "-1"))
            except ValueError:
                continue
            if confidence >= 0:
                scores.append(confidence / 100)
        if not scores:
            return 0.0
        return sum(scores) / len(scores)

    def _extract_metric(self, field: str, lines: list[str], base_confidence: float) -> MetricValue:
        labels = LABELS[field]
        for line in lines:
            lowered = _normalize_label(line)
            for label in labels:
                if label in lowered:
                    number = self._first_value_after_label(line, label)
                    value = self._parse_value(number, field) if number else None
                    confidence = min(0.98, base_confidence + 0.08) if value is not None else 0.0
                    return MetricValue(value=value, raw_text=line, confidence=confidence)
        return MetricValue(value=None, raw_text="", confidence=0.0)

    def _first_value_after_label(self, line: str, label: str) -> str | None:
        normalized = _normalize_label(line)
        position = normalized.find(label)
        search_text = line[position + len(label):] if position >= 0 else line
        match = NUMBER_RE.search(search_text)
        if match:
            return match.group(0)
        all_matches = NUMBER_RE.findall(line)
        return all_matches[-1] if all_matches else None

    def _parse_value(self, raw: str | None, field: str) -> float | int | None:
        if raw is None:
            return None
        if field in PERCENT_FIELDS and "%" not in raw:
            return None
        if field in MONEY_FIELDS and "$" not in raw and "." not in raw and not re.search(r"\d+,\d{2}$", raw.strip()):
            return None
        if field in COUNT_FIELDS and not re.fullmatch(r"\d[\d,]*", raw.strip()):
            return None
        cleaned = raw.strip().replace("$", "")
        if "." not in cleaned and cleaned.count(",") == 1 and len(cleaned.rsplit(",", 1)[1].strip(")%")) == 2:
            cleaned = cleaned.replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
        negative = cleaned.startswith("(") and cleaned.endswith(")")
        cleaned = cleaned.strip("()%")
        try:
            value = float(cleaned)
        except ValueError:
            return None
        if negative:
            value *= -1
        if field in PERCENT_FIELDS:
            return value
        if field in MONEY_FIELDS or "." in cleaned:
            return round(value, 2)
        return int(value)

    def _parse_overview_value_row(self, region: OcrRegion) -> dict[str, MetricValue]:
        parsed: dict[str, MetricValue] = {}
        for line in region.text.splitlines():
            values = NUMBER_RE.findall(line)
            if len(values) < 4:
                continue
            accounts = self._parse_value(values[0], "accounts_worked")
            attempts = self._parse_value(values[1], "attempts")
            contact_index = next((index for index, value in enumerate(values[2:], start=2) if "%" in value), None)
            contact_rate = self._parse_value(values[contact_index], "contact_rate") if contact_index is not None else None
            close_rate = self._parse_value(values[contact_index + 1], "close_rate") if contact_index is not None and len(values) > contact_index + 1 else None
            dollars_per_contact = self._parse_value(values[contact_index + 2], "dollars_per_contact") if contact_index is not None and len(values) > contact_index + 2 else None
            rpc = self._parse_value(values[2], "rpc") if contact_index == 3 else None
            if accounts is not None:
                parsed["accounts_worked"] = MetricValue(accounts, f"{region.name} values: {values[0]}", region.confidence)
            if attempts is not None:
                parsed["attempts"] = MetricValue(attempts, f"{region.name} values: {values[1]}", region.confidence)
            if rpc is not None:
                parsed["rpc"] = MetricValue(rpc, f"{region.name} values: {values[2]}", region.confidence)
            if contact_rate is not None:
                parsed["contact_rate"] = MetricValue(contact_rate, f"{region.name} values: {values[contact_index]}", region.confidence)
            if close_rate is not None:
                parsed["close_rate"] = MetricValue(close_rate, f"{region.name} values: {values[contact_index + 1]}", region.confidence)
            if dollars_per_contact is not None:
                parsed["dollars_per_contact"] = MetricValue(dollars_per_contact, f"{region.name} values: {values[contact_index + 2]}", region.confidence)
            if parsed:
                return parsed
        return parsed

    def _apply_region_metrics(self, metrics: dict[str, MetricValue], region: OcrRegion) -> None:
        lines = [line.strip() for line in region.text.splitlines() if line.strip()]
        region_metrics: dict[str, MetricValue] = {}
        if region.name == "overview_cards":
            fields = ("accounts_worked", "attempts", "rpc", "contact_rate", "close_rate", "dollars_per_contact")
            region_metrics.update(self._parse_overview_value_row(region) or self._parse_sequence(region, fields))
        elif region.name == "activity_section":
            fields = ("activated", "moved_to_hot", "live_contacts", "no_answer", "left_message", "average_agent")
            region_metrics.update(self._parse_sequence(region, fields))
        elif region.name == "performance_section":
            for field in ("contact_rate", "close_rate", "open_rate", "pay_rate"):
                metric = self._extract_metric(field, lines, region.confidence)
                if metric.value is not None:
                    region_metrics[field] = metric
        elif region.name == "money_section":
            self._apply_collection_summary_fallback(metrics, lines, region.confidence)
            for field in ("posted_cash", "posted_fees", "pending_cash", "pending_fees", "green_cleared_cash", "future_scheduled_cash", "future_scheduled_fees"):
                if metrics[field].value is not None:
                    region_metrics[field] = metrics[field]
        elif region.name == "portfolio_section":
            self._apply_portfolio_table_fallback(metrics, lines, region.confidence)
            for field in ("accounts_worked", "attempts", "rpc", "live_contacts", "contact_rate"):
                if metrics[field].value is not None:
                    region_metrics[field] = metrics[field]
        elif region.name == "full_image":
            self._apply_collection_summary_fallback(metrics, lines, region.confidence)
            self._apply_portfolio_table_fallback(metrics, lines, region.confidence)
            for field in ("posted_cash", "posted_fees", "pending_cash", "pending_fees", "green_cleared_cash", "future_scheduled_cash", "future_scheduled_fees"):
                if metrics[field].value is not None:
                    region_metrics[field] = metrics[field]
            for field in ("accounts_worked", "attempts", "rpc", "live_contacts", "contact_rate"):
                if metrics[field].value is not None:
                    region_metrics[field] = metrics[field]
        else:
            for field in METRIC_FIELDS:
                metric = self._extract_metric(field, lines, region.confidence)
                if metric.value is not None:
                    region_metrics[field] = metric

        object.__setattr__(region, "parsed_metrics", region_metrics)
        for field, metric in region_metrics.items():
            if metrics[field].value is None:
                metrics[field] = metric

    def _write_debug_artifacts(
        self,
        original_path: Path,
        debug_dir: Path,
        regions: list[OcrRegion],
        image_size: tuple[int, int],
        report: ExtractedReport,
    ) -> None:
        debug_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original_path, debug_dir / "original.png")
        full_region = next((region for region in regions if region.name == "full_image"), None)
        if full_region:
            shutil.copyfile(full_region.path, debug_dir / "full_image_preprocessed.png")

        debug_regions: list[dict[str, Any]] = []
        for region in regions:
            if region.name == "full_image":
                target_name = "full_image_preprocessed.png"
            else:
                target_name = f"{region.name}_crop.png"
                shutil.copyfile(region.path, debug_dir / target_name)
            parsed = {
                field: metric.to_dict()
                for field, metric in region.parsed_metrics.items()
            }
            debug_regions.append(
                {
                    "name": region.name,
                    "file": target_name,
                    "coordinates": {
                        "x": region.coordinates[0],
                        "y": region.coordinates[1],
                        "w": region.coordinates[2],
                        "h": region.coordinates[3],
                    },
                    "relative": region.relative,
                    "confidence": round(region.confidence, 3),
                    "ocr_text": region.text,
                    "parsed_metrics": parsed,
                }
            )

        (debug_dir / "ocr_full_text.txt").write_text(report.ocr_text)
        (debug_dir / "ocr_regions.json").write_text(json.dumps(debug_regions, indent=2, sort_keys=True))
        (debug_dir / "parsed_metrics.json").write_text(
            json.dumps(
                {
                    field: metric.to_dict()
                    for field, metric in report.metrics.items()
                },
                indent=2,
                sort_keys=True,
            )
        )
        (debug_dir / "debug_report.md").write_text(
            self._build_debug_report(original_path, image_size, debug_regions, report)
        )

    def _build_debug_report(
        self,
        screenshot_path: Path,
        image_size: tuple[int, int],
        debug_regions: list[dict[str, Any]],
        report: ExtractedReport,
    ) -> str:
        lines = [
            f"# OCR Debug Report - {report.report_date}",
            "",
            f"- Screenshot path: `{screenshot_path}`",
            f"- Image size: {image_size[0]} x {image_size[1]}",
            f"- Completeness score: {report.completeness_score:.0%}",
            f"- Confidence score: {report.confidence_score:.0%}",
            f"- Quality gate passed: {'yes' if report.passes_quality_gate else 'no'}",
            f"- Failed required fields: {', '.join(report.missing_quality_fields) or 'none'}",
            "",
            "## Parsed Metrics",
            "",
        ]
        for field, metric in report.metrics.items():
            lines.append(f"- {field}: {metric.value!r} (confidence {metric.confidence:.0%}, raw `{metric.raw_text}`)")
        lines.extend(["", "## Regions", ""])
        for region in debug_regions:
            coords = region["coordinates"]
            lines.extend(
                [
                    f"### {region['name']}",
                    "",
                    f"- File: `{region['file']}`",
                    f"- Coordinates: x={coords['x']}, y={coords['y']}, w={coords['w']}, h={coords['h']}",
                    f"- Confidence: {region['confidence']:.0%}",
                    f"- Parsed values: {json.dumps(region['parsed_metrics'], sort_keys=True)}",
                    "",
                    "```text",
                    region["ocr_text"].strip() or "(no OCR text)",
                    "```",
                    "",
                ]
            )
        return "\n".join(lines)

    def _parse_sequence(self, region: OcrRegion, fields: tuple[str, ...]) -> dict[str, MetricValue]:
        values: list[str] = []
        for line in region.text.splitlines():
            values.extend(NUMBER_RE.findall(line))
        parsed: dict[str, MetricValue] = {}
        if len(values) < len(fields):
            return parsed
        for field, raw_value in zip(fields, values[:len(fields)]):
            value = self._parse_value(raw_value, field)
            if value is not None:
                parsed[field] = MetricValue(value=value, raw_text=f"{region.name}: {raw_value}", confidence=region.confidence)
        return parsed

    def _extract_collector_totals(self, lines: list[str]) -> list[dict[str, str | float]]:
        totals: list[dict[str, str | float]] = []
        if not self.collector_codes:
            return totals
        whiteboard_lines = _section(lines, "=== whiteboard ===", "=== full ===")
        if not whiteboard_lines:
            whiteboard_lines = _section(lines, "whiteboard", "current month")
        tokens = _ocr_tokens(whiteboard_lines)
        for index, token in enumerate(tokens):
            normalized = _normalize_collector_line(token)
            for code in self.collector_codes:
                if normalized != code:
                    continue
                raw_amount = _next_numeric_token(tokens, index + 1)
                amount = self._parse_value(raw_amount, "posted_cash")
                if amount is not None:
                    totals.append({"collector": code, "total": amount, "raw_text": token, "source": "whiteboard"})
                break
        if totals:
            return totals[:15]
        for line in whiteboard_lines:
            normalized = _normalize_collector_line(line)
            for code in self.collector_codes:
                if not _collector_code_at_line_start(normalized, code):
                    continue
                if _looks_like_non_collector(normalized):
                    continue
                values = NUMBER_RE.findall(line)
                if not values:
                    continue
                amount = self._parse_value(values[0], "posted_cash")
                if amount is not None:
                    totals.append({"collector": code, "total": amount, "raw_text": line, "source": "whiteboard"})
                break
        return totals[:15]

    def _apply_scollect_overview_fallback(
        self,
        metrics: dict[str, MetricValue],
        lines: list[str],
        base_confidence: float,
    ) -> None:
        overview_values: list[str] = []
        for line in _section(lines, "overview", "run report"):
            if "/" in line:
                continue
            values = NUMBER_RE.findall(line)
            if len(values) >= 5:
                overview_values.extend(values)
        if len(overview_values) < 18:
            return

        ordered_fields = (
            "accounts_worked",
            "attempts",
            "rpc",
            "contact_rate",
            "close_rate",
            "dollars_per_contact",
            "activated",
            "moved_to_hot",
            "live_contacts",
            "no_answer",
            "left_message",
            "average_agent",
            "emails_sent",
            "campaigns",
            "unique_open",
            "unique_link",
            "open_rate",
            "pay_rate",
        )
        confidence = min(0.9, base_confidence + 0.03)
        for field, raw_value in zip(ordered_fields, overview_values[:18]):
            if metrics[field].value is not None:
                continue
            value = self._parse_value(raw_value, field)
            if value is not None:
                metrics[field] = MetricValue(value=value, raw_text="SCollect overview card sequence", confidence=confidence)

    def _apply_collection_summary_fallback(
        self,
        metrics: dict[str, MetricValue],
        lines: list[str],
        base_confidence: float,
    ) -> None:
        confidence = min(0.88, base_confidence)
        tokens = _ocr_tokens(lines)
        self._apply_money_tokens(metrics, tokens, confidence)
        for line in lines:
            lowered = line.lower()
            values = NUMBER_RE.findall(line)
            if lowered.startswith("posted ") and len(values) >= 2 and metrics["posted_cash"].value is None:
                metrics["posted_cash"] = MetricValue(
                    self._parse_value(values[0], "posted_cash"),
                    line,
                    confidence,
                )
                metrics["posted_fees"] = MetricValue(
                    self._parse_value(values[1], "posted_fees"),
                    line,
                    confidence,
                )
            if lowered.startswith("pending ") and len(values) == 2 and metrics["pending_cash"].value is None:
                metrics["pending_cash"] = MetricValue(
                    self._parse_value(values[0], "pending_cash"),
                    line,
                    confidence,
                )
                metrics["pending_fees"] = MetricValue(
                    self._parse_value(values[1], "pending_fees"),
                    line,
                    confidence,
                )
            if lowered.startswith("green ") and len(values) >= 2 and metrics["green_cleared_cash"].value is None:
                metrics["green_cleared_cash"] = MetricValue(
                    self._parse_value(values[0], "green_cleared_cash"),
                    line,
                    confidence,
                )
            if "total future" in lowered and len(values) >= 3:
                metrics["future_scheduled_cash"] = MetricValue(
                    self._parse_value(values[-3], "future_scheduled_cash"),
                    line,
                    confidence,
                )
                metrics["future_scheduled_fees"] = MetricValue(
                    self._parse_value(values[-2], "future_scheduled_fees"),
                    line,
                    confidence,
                )

    def _apply_money_tokens(self, metrics: dict[str, MetricValue], tokens: list[str], confidence: float) -> None:
        for index, token in enumerate(tokens):
            lowered = token.lower()
            if lowered == "posted" and metrics["posted_cash"].value is None:
                values = _next_numeric_tokens(tokens, index + 1, 2)
                if len(values) == 2:
                    cash = self._parse_value(values[0], "posted_cash")
                    fee = self._parse_value(values[1], "posted_fees")
                    if cash is not None and fee is not None:
                        metrics["posted_cash"] = MetricValue(cash, f"money_section tokens: {values[0]}", confidence)
                        metrics["posted_fees"] = MetricValue(fee, f"money_section tokens: {values[1]}", confidence)
            if lowered.startswith("pending") and metrics["pending_cash"].value is None:
                values = NUMBER_RE.findall(token) + _next_numeric_tokens(tokens, index + 1, 2)
                if len(values) >= 2:
                    cash = self._parse_value(values[0], "pending_cash")
                    fee = self._parse_value(values[1], "pending_fees")
                    if cash is not None and fee is not None:
                        metrics["pending_cash"] = MetricValue(cash, f"money_section tokens: {values[0]}", confidence)
                        metrics["pending_fees"] = MetricValue(fee, f"money_section tokens: {values[1]}", confidence)
            if lowered == "green" and metrics["green_cleared_cash"].value is None:
                values = _next_numeric_tokens(tokens, index + 1, 2)
                if values:
                    cash = self._parse_value(values[0], "green_cleared_cash")
                    if cash is not None:
                        metrics["green_cleared_cash"] = MetricValue(cash, f"money_section tokens: {values[0]}", confidence)
            if lowered == "total future":
                values = _next_numeric_tokens(tokens, index + 1, 3)
                if len(values) >= 2:
                    cash = self._parse_value(values[0], "future_scheduled_cash")
                    fee = self._parse_value(values[1], "future_scheduled_fees")
                    if cash is not None and fee is not None:
                        metrics["future_scheduled_cash"] = MetricValue(cash, f"money_section tokens: {values[0]}", confidence)
                        metrics["future_scheduled_fees"] = MetricValue(fee, f"money_section tokens: {values[1]}", confidence)

    def _apply_portfolio_table_fallback(
        self,
        metrics: dict[str, MetricValue],
        lines: list[str],
        base_confidence: float,
    ) -> None:
        rows = self._extract_portfolio_rows(lines)
        if not rows:
            return

        accounts_worked = sum(row["accounts_worked"] for row in rows)
        attempts = sum(row["attempts"] for row in rows)
        rpc = sum(row["rpc"] for row in rows)
        confidence = min(0.9, base_confidence + 0.04)

        if accounts_worked and self._should_replace_metric(metrics["accounts_worked"], accounts_worked):
            metrics["accounts_worked"] = MetricValue(accounts_worked, "Collections By Portfolio table", confidence)
        if attempts and self._should_replace_metric(metrics["attempts"], attempts):
            metrics["attempts"] = MetricValue(attempts, "Collections By Portfolio table", confidence)
        if rpc and self._should_replace_metric(metrics["rpc"], rpc):
            metrics["rpc"] = MetricValue(rpc, "Collections By Portfolio table", confidence)
        if rpc and self._should_replace_metric(metrics["live_contacts"], rpc):
            metrics["live_contacts"] = MetricValue(rpc, "Collections By Portfolio rpc total", confidence)
        if attempts and rpc and metrics["contact_rate"].value is None:
            metrics["contact_rate"] = MetricValue(
                round((rpc / attempts) * 100, 2),
                "Calculated from Collections By Portfolio rpc / attempts",
                confidence,
            )

    def _extract_portfolio_rows(self, lines: list[str]) -> list[dict[str, int]]:
        section = _section(lines, "collections by portfolio", "quick reports")
        if not section:
            section = lines

        rows: list[dict[str, int]] = []
        current: list[str] = []
        for line in section:
            if _portfolio_row_start(line):
                if current:
                    parsed = self._parse_portfolio_row(current)
                    if parsed:
                        rows.append(parsed)
                current = [line]
            elif current:
                current.append(line)
        if current:
            parsed = self._parse_portfolio_row(current)
            if parsed:
                rows.append(parsed)
        return rows

    def _parse_portfolio_row(self, row_lines: list[str]) -> dict[str, int] | None:
        text = "\n".join(row_lines)
        cleaned = re.sub(r"\b(?:CS|LN)\d+\b", "", text, flags=re.IGNORECASE)
        cleaned = re.sub(r"\bGreen\s*Wave\s+[A-Z]\b", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\bGreenWave\s+[A-Z]\b", "", cleaned, flags=re.IGNORECASE)
        values = NUMBER_RE.findall(cleaned)
        if len(values) < 2:
            return None

        accounts_worked = self._parse_value(values[0], "accounts_worked")
        if accounts_worked is None:
            return None

        attempts_index = 2 if len(values) >= 3 and values[1].replace(",", "") == "0" else 1
        attempts = self._parse_value(values[attempts_index], "attempts")
        if attempts is None:
            return None

        rpc = 0
        if len(values) > attempts_index + 2 and "." in values[attempts_index + 1]:
            possible_rpc = self._parse_value(values[attempts_index + 2], "rpc")
            if isinstance(possible_rpc, int) and 0 < possible_rpc <= 5:
                rpc = possible_rpc

        return {
            "accounts_worked": int(accounts_worked),
            "attempts": int(attempts),
            "rpc": int(rpc),
        }

    def _should_replace_metric(self, metric: MetricValue, replacement: int | float) -> bool:
        if metric.value is None:
            return True
        if isinstance(metric.value, (int, float)) and metric.value <= 1 < replacement:
            return True
        return metric.confidence < self.min_confidence

    def _apply_derived_metrics(self, metrics: dict[str, MetricValue]) -> None:
        attempts = metrics["attempts"].value
        rpc = metrics["rpc"].value
        live_contacts = metrics["live_contacts"].value

        if (
            isinstance(rpc, int)
            and (live_contacts is None or metrics["live_contacts"].confidence < self.min_confidence)
        ):
            metrics["live_contacts"] = MetricValue(rpc, "Derived from RPC", metrics["rpc"].confidence)
            live_contacts = rpc

        if (
            metrics["contact_rate"].value is None
            and isinstance(attempts, int)
            and attempts > 0
            and isinstance(live_contacts, int)
        ):
            metrics["contact_rate"] = MetricValue(
                round((live_contacts / attempts) * 100, 2),
                "Calculated from live contacts / attempts",
                min(metrics["attempts"].confidence, metrics["live_contacts"].confidence),
            )


def _normalize_label(value: str) -> str:
    return re.sub(r"\s+", " ", value.lower().replace("|", " ").replace(":", " ")).strip()


def _section(lines: list[str], start_text: str, end_text: str) -> list[str]:
    start = 0
    end = len(lines)
    for index, line in enumerate(lines):
        if start_text in line.lower():
            start = index + 1
            break
    for index in range(start, len(lines)):
        if end_text in lines[index].lower():
            end = index
            break
    return lines[start:end]


def _normalize_collector_line(value: str) -> str:
    return re.sub(r"\s+", " ", value.upper().replace("|", " ")).strip()


def _collector_code_at_line_start(line: str, code: str) -> bool:
    escaped = re.escape(code.upper())
    return bool(re.match(rf"^{escaped}(?:\s|$)", line))


def _portfolio_row_start(line: str) -> bool:
    normalized = line.strip()
    return bool(
        re.match(r"^(Green\s*Wave|GreenWave)\s+[A-Z]\b", normalized, flags=re.IGNORECASE)
        or re.match(r"^(CS|LN)\d+\b", normalized, flags=re.IGNORECASE)
    )


def _looks_like_non_collector(value: str) -> bool:
    if "," in value:
        return True
    if re.search(r"\b[A-Z]{2}\s+\d{5}\b", value):
        return True
    if re.search(r"\b\d{3}[-.) ]\d{3}[-. ]\d{4}\b", value):
        return True
    blocked = {
        "ATLANTA",
        "GA",
        "CASH",
        "FEE",
        "COUNT",
        "POSTED",
        "PENDING",
        "FUTURE",
        "CURRENT MONTH",
        "TOTAL FUTURE",
        "DASHBOARD",
    }
    tokens = set(re.findall(r"[A-Z]+", value))
    return bool(tokens & blocked)


def _ocr_tokens(lines: list[str]) -> list[str]:
    tokens: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if NUMBER_RE.fullmatch(stripped) or re.fullmatch(r"[A-Za-z ]+", stripped):
            tokens.append(stripped)
            continue
        tokens.extend(part.strip() for part in re.split(r"\s{2,}|\|", stripped) if part.strip())
    return tokens


def _next_numeric_token(tokens: list[str], start: int) -> str | None:
    values = _next_numeric_tokens(tokens, start, 1)
    return values[0] if values else None


def _next_numeric_tokens(tokens: list[str], start: int, limit: int) -> list[str]:
    values: list[str] = []
    for token in tokens[start:]:
        matches = NUMBER_RE.findall(token)
        for match in matches:
            values.append(match)
            if len(values) >= limit:
                return values
    return values
