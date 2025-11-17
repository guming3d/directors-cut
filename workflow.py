"""Term Extraction Workflow using Agent Framework.

This workflow ingests a two-column Excel worksheet (Speaker, Speech),
chunks the dialog based on an approximate token budget, persists the
chunks for inspection, calls Azure OpenAI to extract terminology using
the provided XML prompt, and finally writes a deduplicated terminology
XML file.
"""

import argparse
import asyncio
import logging
import os
import posixpath
import zipfile
from dataclasses import dataclass
from io import BytesIO
from typing import Any, Optional
import xml.etree.ElementTree as ET

from pydantic import BaseModel, Field

from agent_framework import Executor, WorkflowBuilder, WorkflowContext, handler

# Load environment variables early so defaults pick them up.
try:
    from dotenv import load_dotenv, find_dotenv  # type: ignore

    _DOTENV = find_dotenv(usecwd=True)
    if _DOTENV:
        load_dotenv(_DOTENV, override=True)
    else:
        load_dotenv(override=True)
except Exception:
    pass


def _configure_logging() -> logging.Logger:
    level_name = (os.getenv("LOG_LEVEL") or "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    if not isinstance(level, int):
        level = logging.INFO
    log_dir = os.getenv("LOG_DIR", "log")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, "workflow.log")

    logger = logging.getLogger("term_workflow")
    if logger.handlers:
        return logger
    logger.setLevel(level)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    logger.propagate = False
    logger.debug("Logger initialised at level %s; writing to %s", logging.getLevelName(level), log_path)
    return logger


logger = _configure_logging()


# -------------------------
# Models
# -------------------------


class TermExtractionRequest(BaseModel):
    input_path: str = Field(default="test.xlsx", description="Excel file containing dialog (Speaker, Speech)")
    role_list_path: str | None = Field(default=None, description="Optional XML role list to exclude from terms")
    workbook_password: str | None = Field(
        default=None, description="Optional password for encrypted Excel workbooks"
    )
    chunk_token_size: int = Field(default=5000, ge=500, description="Approximate token budget per chunk")
    chunk_output_dir: str = Field(default="dialog_chunks", description="Directory to write chunk text files")
    terms_output_path: str = Field(default="extracted_terms.xml", description="Final XML file for unique terms")
    source_lang: str = Field(default="en", description="Source language code for prompt substitution")
    deployment_name: str = Field(
        default=os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-5"),
        description="Azure OpenAI deployment name (e.g., gpt-5)",
    )


@dataclass
class DialogueLine:
    speaker: str
    utterance: str


@dataclass
class DialogueDataset:
    lines: list[DialogueLine]


@dataclass
class DialogueChunk:
    chunk_id: str
    lines: list[DialogueLine]
    content: str
    token_estimate: int


@dataclass
class TermEntry:
    term: str
    notes: str | None = None


@dataclass
class ChunkTerms:
    chunk_id: str
    terms: list[TermEntry]


# -------------------------
# Azure OpenAI helpers
# -------------------------


class AzureLLM:
    """Thin wrapper around Azure OpenAI Responses API."""

    def __init__(self, deployment_name: str) -> None:
        self.deployment = deployment_name
        self.endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "")
        self.api_key = os.getenv("AZURE_OPENAI_API_KEY", "")
        self.api_version = os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-15-preview")
        self._defaults = _load_chat_defaults()
        self._client = None

    def _ensure_client(self) -> None:
        if self._client is None:
            from openai import AzureOpenAI  # type: ignore

            self._client = AzureOpenAI(
                api_key=self.api_key,
                api_version=self.api_version,
                azure_endpoint=self.endpoint,
            )

    def chat(self, messages: list[dict[str, Any]], overrides: dict[str, Any] | None = None) -> str:
        self._ensure_client()
        payload: dict[str, Any] = {
            "model": self.deployment,
            "input": _format_responses_input(messages),
        }
        _merge_options(payload, self._defaults)
        if overrides:
            _merge_options(payload, overrides)
        logger.debug("AzureLLM chat payload: %s", payload)
        response = self._client.responses.create(**payload)
        logger.debug("AzureLLM chat response: %s", response)
        return _extract_output_text(response)


async def llm_chat_async(llm: AzureLLM, messages: list[dict[str, Any]], overrides: dict[str, Any] | None = None) -> str:
    return await asyncio.to_thread(llm.chat, messages, overrides)


def _load_chat_defaults() -> dict[str, Any]:
    defaults: dict[str, Any] = {}
    max_tokens = _env_int("AZURE_OPENAI_MAX_COMPLETION_TOKENS")
    if max_tokens is not None:
        defaults["max_output_tokens"] = max_tokens
    reasoning_effort = os.getenv("AZURE_OPENAI_REASONING_EFFORT")
    if reasoning_effort:
        defaults["reasoning"] = {"effort": reasoning_effort}
    verbosity = os.getenv("AZURE_OPENAI_VERBOSITY")
    if verbosity:
        defaults["text"] = {"verbosity": verbosity}
    return defaults


def _merge_options(target: dict[str, Any], source: dict[str, Any]) -> None:
    for key, value in source.items():
        if key in target and isinstance(target[key], dict) and isinstance(value, dict):
            merged = dict(target[key])
            merged.update(value)
            target[key] = merged
        else:
            target[key] = value


def _format_responses_input(messages: list[dict[str, Any]] | str | None) -> Any:
    if messages is None:
        return ""
    if isinstance(messages, str):
        return [
            {
                "role": "user",
                "content": [{"type": "input_text", "text": messages}],
            }
        ]
    formatted: list[dict[str, Any]] = []
    for message in messages:
        if not isinstance(message, dict):
            formatted.append(
                {"role": "user", "content": _normalize_message_content(message, role="user")}
            )
            continue
        role = message.get("role", "user")
        content = message.get("content")
        formatted.append({"role": role, "content": _normalize_message_content(content, role=role)})
    return formatted


def _normalize_message_content(content: Any, role: str = "user") -> list[dict[str, Any]]:
    default_type = "output_text" if role == "assistant" else "input_text"
    if content is None:
        return [{"type": default_type, "text": ""}]
    if isinstance(content, str):
        return [{"type": default_type, "text": content}]
    if isinstance(content, list):
        items: list[dict[str, Any]] = []
        for item in content:
            if isinstance(item, str):
                items.append({"type": default_type, "text": item})
            elif isinstance(item, dict):
                if "type" in item:
                    items.append(item)
                elif "text" in item and isinstance(item["text"], str):
                    items.append({"type": default_type, "text": item["text"]})
            elif item is not None:
                items.append({"type": default_type, "text": str(item)})
        return items or [{"type": default_type, "text": ""}]
    if isinstance(content, dict):
        if "type" in content:
            return [content]
        text_value = content.get("text")
        if isinstance(text_value, str):
            return [{"type": default_type, "text": text_value}]
    return [{"type": default_type, "text": str(content)}]


def _extract_output_text(response: Any) -> str:
    output_text: Optional[str] = getattr(response, "output_text", None)
    if isinstance(output_text, str) and output_text:
        return output_text

    chunks: list[str] = []
    output = getattr(response, "output", None)
    if output:
        for item in output:
            content = getattr(item, "content", None)
            if not content:
                continue
            for piece in content:
                text = getattr(piece, "text", None)
                if isinstance(text, str):
                    chunks.append(text)

    if not chunks:
        data = getattr(response, "data", None)
        if data:
            for datum in data:
                content = getattr(datum, "content", None)
                if not content:
                    continue
                for piece in content:
                    text = getattr(piece, "text", None)
                    if isinstance(text, str):
                        chunks.append(text)

    return "".join(chunks)


def load_stage_chat_overrides(stage: str, fallback_max: int | None = None) -> dict[str, Any]:
    prefix = f"AZURE_OPENAI_{stage.upper()}_"
    overrides: dict[str, Any] = {}
    max_tokens = _env_int(f"{prefix}MAX_COMPLETION_TOKENS")
    if max_tokens is not None:
        overrides["max_output_tokens"] = max_tokens
    elif fallback_max is not None:
        overrides["max_output_tokens"] = fallback_max
    reasoning_effort = os.getenv(f"{prefix}REASONING_EFFORT")
    if reasoning_effort:
        overrides["reasoning"] = {"effort": reasoning_effort}
    verbosity = os.getenv(f"{prefix}VERBOSITY")
    if verbosity:
        overrides["text"] = {"verbosity": verbosity}
    return overrides


def _env_int(name: str) -> int | None:
    value = os.getenv(name)
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


# -------------------------
# Utility functions
# -------------------------


MAIN_NS = {"main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
REL_NS = {"rel": "http://schemas.openxmlformats.org/package/2006/relationships"}


def load_text(path: str | None) -> str:
    if not path or not os.path.exists(path):
        return ""
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def escape_xml(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def indent_xml(element: ET.Element, level: int = 0) -> None:
    """Recursively add indentation to an XML element for pretty-print output."""
    indent_str = "\n" + "  " * level
    if len(element):
        if not element.text or not element.text.strip():
            element.text = indent_str + "  "
        for idx, child in enumerate(element):
            indent_xml(child, level + 1)
            if not child.tail or not child.tail.strip():
                child.tail = indent_str + ("  " if idx != len(element) - 1 else "")
        if not element[-1].tail or not element[-1].tail.strip():
            element[-1].tail = indent_str
    elif level and (not element.tail or not element.tail.strip()):
        element.tail = indent_str


def render_prompt(template_text: str, mapping: dict[str, str]) -> str:
    rendered = template_text
    for key, value in mapping.items():
        rendered = rendered.replace("{" + key + "}", value)
    return rendered


def clean_fenced_text(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = []
        for line in stripped.splitlines():
            if line.strip().startswith("```"):
                continue
            lines.append(line)
        return "\n".join(lines).strip()
    return stripped


def estimate_tokens(text: str) -> int:
    if not text:
        return 1
    word_tokens = len(text.split())
    char_tokens = len(text) // 4
    return max(1, max(word_tokens, char_tokens))


def _read_shared_strings(zf: zipfile.ZipFile) -> list[str]:
    try:
        with zf.open("xl/sharedStrings.xml") as fh:
            tree = ET.parse(fh)
    except KeyError:
        return []
    strings: list[str] = []
    for si in tree.getroot().findall(".//main:si", namespaces=MAIN_NS):
        text_parts = []
        for node in si.findall(".//main:t", namespaces=MAIN_NS):
            text_parts.append(node.text or "")
        strings.append("".join(text_parts))
    return strings


def _resolve_first_sheet(zf: zipfile.ZipFile) -> str:
    with zf.open("xl/workbook.xml") as fh:
        wb_tree = ET.parse(fh)
    sheet = wb_tree.find("main:sheets/main:sheet", namespaces=MAIN_NS)
    if sheet is None:
        raise ValueError("No sheets found in workbook")
    rel_id = sheet.attrib.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
    if not rel_id:
        return "xl/worksheets/sheet1.xml"
    with zf.open("xl/_rels/workbook.xml.rels") as fh:
        rel_tree = ET.parse(fh)
    for rel in rel_tree.getroot().findall("rel:Relationship", namespaces=REL_NS):
        if rel.attrib.get("Id") == rel_id:
            target = rel.attrib.get("Target", "")
            if target.startswith("/"):
                target = target[1:]
            if not target.startswith("xl/"):
                return posixpath.join("xl", target)
            return target
    return "xl/worksheets/sheet1.xml"


def _cell_value(cell: ET.Element, shared_strings: list[str]) -> str:
    cell_type = cell.attrib.get("t")
    if cell_type == "s":
        v = cell.find("main:v", namespaces=MAIN_NS)
        if v is not None and v.text is not None:
            idx = int(v.text)
            if 0 <= idx < len(shared_strings):
                return shared_strings[idx]
        return ""
    if cell_type == "inlineStr":
        t_node = cell.find(".//main:t", namespaces=MAIN_NS)
        return (t_node.text or "") if t_node is not None else ""
    v_node = cell.find("main:v", namespaces=MAIN_NS)
    if v_node is None or v_node.text is None:
        return ""
    return v_node.text


def _column_label(cell_ref: str) -> str:
    label = "".join(ch for ch in cell_ref if ch.isalpha())
    return label.upper() or "A"


def load_excel_dialog(path: str, password: str | None = None) -> list[DialogueLine]:
    if not os.path.exists(path):
        raise FileNotFoundError(f"Input Excel not found: {path}")
    logger.info("Loading dialog from %s", path)
    if zipfile.is_zipfile(path):
        logger.debug("Detected XLSX archive for %s", path)
        with zipfile.ZipFile(path) as zf:
            return _load_excel_dialog_xlsx_from_zip(zf)

    xlrd_error: Exception | None = None
    xlrd_missing = False
    try:
        logger.debug("Attempting to read legacy XLS via xlrd for %s", path)
        return _load_excel_dialog_xls(path)
    except ImportError:
        xlrd_missing = True
    except Exception as exc:
        xlrd_error = exc
        if not _is_xlrd_encryption_error(exc):
            logger.error("Failed to parse Excel file %s: %s", path, exc)
            raise RuntimeError(f"Unable to parse Excel file at {path}") from exc

    decrypted_stream = _try_decrypt_excel(path, password)
    if decrypted_stream is None:
        if xlrd_error is not None:
            raise RuntimeError(
                "Excel file appears to be encrypted. Install msoffcrypto-tool and provide workbook_password if needed."
            ) from xlrd_error
        if xlrd_missing:
            raise RuntimeError(
                "Reading legacy XLS workbooks requires the xlrd package. Install it and retry."
            )
        raise RuntimeError(
            "Excel file format not recognized. Ensure the workbook is a valid XLSX/XLS file or install required dependencies."
        )

    if _stream_is_zip(decrypted_stream):
        decrypted_stream.seek(0)
        logger.info("Decrypted workbook %s as XLSX", path)
        with zipfile.ZipFile(decrypted_stream) as zf:
            return _load_excel_dialog_xlsx_from_zip(zf)
    decrypted_stream.seek(0)
    logger.info("Decrypted workbook %s as XLS", path)
    return _load_excel_dialog_xls_stream(decrypted_stream)


def _load_excel_dialog_xlsx(path: str) -> list[DialogueLine]:
    with zipfile.ZipFile(path) as zf:
        return _load_excel_dialog_xlsx_from_zip(zf)


def _load_excel_dialog_xlsx_from_zip(zf: zipfile.ZipFile) -> list[DialogueLine]:
    shared_strings = _read_shared_strings(zf)
    sheet_path = _resolve_first_sheet(zf)
    with zf.open(sheet_path) as fh:
        tree = ET.parse(fh)
    return _parse_dialog_rows(tree.getroot(), shared_strings)


def _parse_dialog_rows(sheet_root: ET.Element, shared_strings: list[str]) -> list[DialogueLine]:
    dialog: list[DialogueLine] = []
    for row in sheet_root.findall(".//main:sheetData/main:row", namespaces=MAIN_NS):
        speaker = ""
        utterance = ""
        for cell in row.findall("main:c", namespaces=MAIN_NS):
            ref = cell.attrib.get("r", "A")
            column = _column_label(ref)
            value = (_cell_value(cell, shared_strings) or "").strip()
            if column == "A":
                speaker = value
            elif column == "B":
                utterance = value
            if speaker and utterance:
                break
        if not speaker and not utterance:
            continue
        if not dialog and speaker.lower() in {"speaker", "speaker name"}:
            if utterance.lower() in {"speech", "dialogue", "text"}:
                continue
        dialog.append(DialogueLine(speaker=speaker, utterance=utterance))
    logger.debug("Parsed %d dialogue rows from XML sheet", len(dialog))
    return dialog


def _load_excel_dialog_xls(path: str) -> list[DialogueLine]:
    import xlrd  # type: ignore

    workbook = xlrd.open_workbook(path)
    sheet = workbook.sheet_by_index(0)
    return _parse_xlrd_sheet(sheet)


def _load_excel_dialog_xls_stream(stream: BytesIO) -> list[DialogueLine]:
    import xlrd  # type: ignore

    stream.seek(0)
    data = stream.read()
    workbook = xlrd.open_workbook(file_contents=data)
    sheet = workbook.sheet_by_index(0)
    return _parse_xlrd_sheet(sheet)


def _parse_xlrd_sheet(sheet: Any) -> list[DialogueLine]:
    dialog: list[DialogueLine] = []
    for row_idx in range(sheet.nrows):
        speaker_cell = sheet.cell_value(row_idx, 0) if sheet.ncols >= 1 else ""
        utterance_cell = sheet.cell_value(row_idx, 1) if sheet.ncols >= 2 else ""
        speaker = _coerce_cell_to_str(speaker_cell)
        utterance = _coerce_cell_to_str(utterance_cell)
        if not speaker and not utterance:
            continue
        if not dialog and speaker.lower() in {"speaker", "speaker name"}:
            if utterance.lower() in {"speech", "dialogue", "text"}:
                continue
        dialog.append(DialogueLine(speaker=speaker, utterance=utterance))
    logger.debug("Parsed %d dialogue rows from xlrd sheet", len(dialog))
    return dialog


def _coerce_cell_to_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        return str(value)
    return str(value).strip()


def _is_xlrd_encryption_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(token in message for token in ["encrypted", "password", "compound document", "ole2", "crypto"])


def _try_decrypt_excel(path: str, password: str | None) -> BytesIO | None:
    try:
        import msoffcrypto  # type: ignore
    except ImportError:
        logger.debug("msoffcrypto-tool not installed; cannot attempt decryption for %s", path)
        return None

    decrypted = BytesIO()
    with open(path, "rb") as fh:
        office_file = msoffcrypto.OfficeFile(fh)
        is_encrypted = True
        if hasattr(office_file, "is_encrypted"):
            try:
                is_encrypted = office_file.is_encrypted()  # type: ignore[call-arg]
            except Exception:
                is_encrypted = True
        if not is_encrypted:
            logger.debug("Workbook %s is not encrypted; skipping decryption", path)
            return None
        logger.info("Attempting to decrypt workbook %s", path)
        try:
            office_file.load_key(password=password or "")
        except Exception as exc:
            raise RuntimeError(
                "Failed to unlock encrypted Excel workbook. Check the workbook_password setting."
            ) from exc
        office_file.decrypt(decrypted)
    logger.info("Successfully decrypted workbook %s", path)
    decrypted.seek(0)
    return decrypted


def _stream_is_zip(stream: BytesIO) -> bool:
    pos = stream.tell()
    stream.seek(0)
    try:
        return zipfile.is_zipfile(stream)
    finally:
        stream.seek(pos)


def build_chunk(lines: list[DialogueLine], index: int) -> DialogueChunk:
    rendered_lines = [f"{line.speaker}:{line.utterance}" for line in lines]
    rendered_text = "\n".join(rendered_lines).strip()
    token_estimate = sum(estimate_tokens(line) for line in rendered_lines)
    return DialogueChunk(
        chunk_id=f"chunk_{index}",
        lines=lines,
        content=rendered_text,
        token_estimate=token_estimate,
    )


def chunk_dialog(dialog: list[DialogueLine], max_tokens: int) -> list[DialogueChunk]:
    chunks: list[DialogueChunk] = []
    current: list[DialogueLine] = []
    token_budget = 0
    for line in dialog:
        rendered = f"{line.speaker}:{line.utterance}"
        line_tokens = estimate_tokens(rendered)
        if current and token_budget + line_tokens > max_tokens:
            chunks.append(build_chunk(current, len(chunks) + 1))
            current = []
            token_budget = 0
        current.append(line)
        token_budget += line_tokens
    if current:
        chunks.append(build_chunk(current, len(chunks) + 1))
    return chunks


def chunk_dialog_as_xml(chunk: DialogueChunk) -> str:
    lines = []
    for idx, line in enumerate(chunk.lines, start=1):
        lines.append(
            f'  <Line index="{idx}" speaker="{escape_xml(line.speaker)}">{escape_xml(line.utterance)}</Line>'
        )
    return "\n".join(lines)


def parse_terms(xml_text: str) -> list[TermEntry]:
    cleaned = clean_fenced_text(xml_text)
    try:
        root = ET.fromstring(cleaned)
    except ET.ParseError:
        return []
    entries: list[TermEntry] = []
    for term_node in root.findall(".//Term"):
        source = ""
        for tag in ("Source", "English", "Text"):
            candidate = term_node.find(tag)
            if candidate is not None and candidate.text:
                source = candidate.text.strip()
                if source:
                    break
        if not source and not list(term_node):
            text_content = term_node.text or ""
            source = text_content.strip()
        notes_value = None
        notes_node = term_node.find("Notes")
        if notes_node is not None and notes_node.text:
            notes_value = notes_node.text.strip() or None
        if source:
            entries.append(TermEntry(term=source, notes=notes_value))
    return entries


# -------------------------
# Executors
# -------------------------


class LoadDialog(Executor):
    """Read Excel dialog and seed shared state."""

    @handler
    async def handle(self, request: TermExtractionRequest, ctx: WorkflowContext[DialogueDataset]) -> None:
        await ctx.set_shared_state("request", request)
        dialog = load_excel_dialog(request.input_path, password=request.workbook_password)
        if not dialog:
            logger.warning("No dialog rows found in %s", request.input_path)
            await ctx.yield_output("No dialog rows found in Excel.")
            return
        logger.info("Loaded %d dialogue lines from %s", len(dialog), request.input_path)
        role_list = load_text(request.role_list_path)
        template_text = load_text("terminology.xml")
        if not template_text:
            raise FileNotFoundError("terminology.xml not found in repository.")
        await ctx.set_shared_state("role_list_xml", role_list)
        await ctx.set_shared_state("terminology_prompt_template", template_text)
        await ctx.send_message(DialogueDataset(lines=dialog))


class ChunkDialog(Executor):
    """Chunk dialog lines based on approximate token limits."""

    @handler
    async def handle(self, dataset: DialogueDataset, ctx: WorkflowContext[DialogueChunk]) -> None:
        request: TermExtractionRequest = await ctx.get_shared_state("request")  # type: ignore
        chunks = chunk_dialog(dataset.lines, request.chunk_token_size)
        await ctx.set_shared_state("total_chunks", len(chunks))
        if not chunks:
            logger.warning("Chunking produced no output; check chunk_token_size=%s", request.chunk_token_size)
            await ctx.yield_output("No chunks generated from dialog.")
            return
        logger.info("Chunked dialog into %d chunk(s) using token size %s", len(chunks), request.chunk_token_size)
        for chunk in chunks:
            await ctx.send_message(chunk)


class PersistChunk(Executor):
    """Write chunk text to disk for inspection."""

    @handler
    async def handle(self, chunk: DialogueChunk, ctx: WorkflowContext[DialogueChunk]) -> None:
        request: TermExtractionRequest = await ctx.get_shared_state("request")  # type: ignore
        output_dir = os.path.abspath(request.chunk_output_dir)
        os.makedirs(output_dir, exist_ok=True)
        file_path = os.path.join(output_dir, f"{chunk.chunk_id}.txt")
        with open(file_path, "w", encoding="utf-8") as fh:
            fh.write(chunk.content + "\n")
        await ctx.set_shared_state(f"path_{chunk.chunk_id}", file_path)
        logger.info("Persisted %s with %d lines to %s", chunk.chunk_id, len(chunk.lines), file_path)
        await ctx.send_message(chunk)


class ExtractTerms(Executor):
    """Invoke Azure OpenAI to extract terminology for a chunk."""

    @handler
    async def handle(self, chunk: DialogueChunk, ctx: WorkflowContext[ChunkTerms]) -> None:
        request: TermExtractionRequest = await ctx.get_shared_state("request")  # type: ignore
        template: str = await ctx.get_shared_state("terminology_prompt_template")  # type: ignore
        role_list: str = await ctx.get_shared_state("role_list_xml") or ""  # type: ignore

        prompt = render_prompt(
            template,
            {
                "org_lang": request.source_lang,
                "Dialog List": chunk_dialog_as_xml(chunk),
                "Role List": role_list,
            },
        )

        llm = AzureLLM(request.deployment_name)
        logger.info("Requesting terminology extraction for %s (≈%d tokens)", chunk.chunk_id, chunk.token_estimate)
        logger.info("Prompt for %s:\n%s", chunk.chunk_id, prompt)
        
        max_tokens = _env_int("AZURE_OPENAI_MAX_COMPLETION_TOKENS") or 8000
        response = await llm_chat_async(
            llm,
            messages=[{"role": "system", "content": prompt}],
            overrides={"max_output_tokens": max_tokens},
        )
        logger.info("Raw response for %s:\n%s", chunk.chunk_id, response)
        terms = parse_terms(response)
        logger.info("Received %d term(s) for %s", len(terms), chunk.chunk_id)
        await ctx.send_message(ChunkTerms(chunk_id=chunk.chunk_id, terms=terms))


class FinalizeTerms(Executor):
    """Aggregate unique terms and write the XML output."""

    @handler
    async def handle(self, chunk_terms: ChunkTerms, ctx: WorkflowContext[None, str]) -> None:  # type: ignore
        try:
            aggregated = await ctx.get_shared_state("aggregated_terms")  # type: ignore
        except KeyError:
            aggregated = {}
        for entry in chunk_terms.terms:
            key = entry.term.strip().lower()
            if not key:
                continue
            existing = aggregated.get(key)
            if existing is None:
                aggregated[key] = entry
            elif not existing.notes and entry.notes:
                existing.notes = entry.notes
        await ctx.set_shared_state("aggregated_terms", aggregated)

        try:
            processed = await ctx.get_shared_state("processed_chunks")  # type: ignore
        except KeyError:
            processed = 0
        processed += 1
        await ctx.set_shared_state("processed_chunks", processed)

        try:
            total = await ctx.get_shared_state("total_chunks")  # type: ignore
        except KeyError:
            total = 0
        if processed < total:
            return

        request: TermExtractionRequest = await ctx.get_shared_state("request")  # type: ignore
        sorted_terms = sorted(
            (entry for entry in aggregated.values()),
            key=lambda e: e.term.lower(),
        )
        root = ET.Element("Terminology_Table")
        for entry in sorted_terms:
            term_element = ET.SubElement(root, "Term")
            source_element = ET.SubElement(term_element, "Source")
            source_element.text = entry.term
            if entry.notes:
                notes_element = ET.SubElement(term_element, "Notes")
                notes_element.text = entry.notes
        indent_xml(root)
        xml_content = '<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(root, encoding="unicode")
        output_dir = os.path.dirname(request.terms_output_path)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
        with open(request.terms_output_path, "w", encoding="utf-8") as fh:
            fh.write(xml_content + "\n")
        logger.info(
            "Wrote %d unique term(s) to %s after processing %d chunk(s)",
            len(sorted_terms),
            request.terms_output_path,
            processed,
        )
        await ctx.yield_output(
            f"Wrote {len(sorted_terms)} unique terms to {request.terms_output_path}"
        )


# -------------------------
# Workflow builder & DevUI
# -------------------------


def create_term_extraction_workflow():
    loader = LoadDialog(id="load_dialog")
    chunker = ChunkDialog(id="chunk_dialog")
    persist = PersistChunk(id="persist_chunk")
    extractor = ExtractTerms(id="extract_terms")
    finalize = FinalizeTerms(id="finalize_terms")

    builder = WorkflowBuilder(
        name="Terminology Extraction Workflow",
        description=(
            "Ingest Excel dialog, chunk by token budget, persist chunks, call Azure OpenAI with terminology prompt, "
            "and produce a deduplicated terminology XML file."
        ),
    )
    (
        builder.set_start_executor(loader)
        .add_edge(loader, chunker)
        .add_edge(chunker, persist)
        .add_edge(persist, extractor)
        .add_edge(extractor, finalize)
    )
    return builder.build()


workflow = create_term_extraction_workflow()


async def _execute_workflow(request: TermExtractionRequest) -> None:
    """Run the terminology workflow once and print any outputs."""
    result = await workflow.run(request)
    outputs = result.get_outputs()
    for output in outputs:
        if output:
            print(output)


def main() -> None:
    parser = argparse.ArgumentParser(description="Terminology extraction workflow runner.")
    parser.add_argument(
        "--input-path",
        default="test.xlsx",
        help="Excel file path containing dialog data (defaults to test.xlsx).",
    )
    parser.add_argument(
        "--devui",
        action="store_true",
        help="Launch the Agent Framework DevUI instead of running the workflow headless.",
    )
    parser.add_argument(
        "--devui-port",
        type=int,
        default=8090,
        help="Port to use when launching the DevUI (only used with --devui).",
    )
    args = parser.parse_args()

    if args.devui:
        from agent_framework.devui import serve

        serve(entities=[workflow], port=args.devui_port, auto_open=True)
        return

    request = TermExtractionRequest(input_path=args.input_path)
    asyncio.run(_execute_workflow(request))


if __name__ == "__main__":
    main()
