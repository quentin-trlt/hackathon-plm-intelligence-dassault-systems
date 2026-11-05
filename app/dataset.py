"""Switch the dataset of the session: upload another XPDMXML extract and, optionally, its enrichment CSV files.

Uploaded files go to a temporary folder that lives as long as the dataset is active in the session. They are never
written to the repository. A dataset that does not load (malformed XML, broken structure, CSV outside the data
contract) is refused and the active one is kept.
"""

from __future__ import annotations

import re
import shutil
import tempfile
from collections.abc import Iterable
from pathlib import Path

import streamlit as st

from plm.parser import (
    ENRICHMENT_FILES,
    MATERIAL_PRICES_FILE,
    PART_SOURCING_FILE,
    SUPPLIERS_FILE,
    VIEWER_FILE,
    EnrichmentError,
    PlmParseError,
    PlmValidationError,
    ViewerError,
)
from services import DATASET_KEY, REFERENCE, Dataset, active_dataset, build_services

UPLOAD_PREFIX = "plm-dataset-"
ENRICHMENT_LABELS = {
    MATERIAL_PRICES_FILE: "Material prices",
    SUPPLIERS_FILE: "Suppliers",
    PART_SOURCING_FILE: "Part sourcing",
}
LOAD_ERRORS = (OSError, PlmParseError, PlmValidationError, EnrichmentError, ViewerError, UnicodeDecodeError)
MAX_PROBLEMS_SHOWN = 15
_UNSAFE = re.compile(r"[^\w.\- ]")


def safe_xml_name(name: str) -> str:
    stem = _UNSAFE.sub("_", Path(name).name).strip(" .") or "extract"
    return stem if stem.lower().endswith(".xml") else f"{stem}.xml"


def precheck(csv_files: Iterable[str]) -> list[str]:
    """Problems visible from the file list alone, before anything is parsed."""
    files = set(csv_files)
    unknown = sorted(files - set(ENRICHMENT_FILES))
    problems = [f"{file} is not an enrichment file, expected one of {list(ENRICHMENT_FILES)}" for file in unknown]
    if PART_SOURCING_FILE in files and SUPPLIERS_FILE not in files:
        problems.append(f"{PART_SOURCING_FILE} refers to suppliers by supplier_id: upload {SUPPLIERS_FILE} with it.")
    return problems


def stage(
    xml_name: str, xml_content: bytes, csv_contents: dict[str, bytes], viewer_content: bytes | None = None
) -> Dataset:
    """Write the files to a new temporary folder, the CSV and 3D view files under their contract names."""
    root = Path(tempfile.mkdtemp(prefix=UPLOAD_PREFIX))
    xml_path = root / safe_xml_name(xml_name)
    xml_path.write_bytes(xml_content)
    enrichment_dir = root / "enrichment"
    enrichment_dir.mkdir()
    for file, content in csv_contents.items():
        (enrichment_dir / file).write_bytes(content)
    viewer_path = None
    if viewer_content is not None:
        viewer_path = root / VIEWER_FILE
        viewer_path.write_bytes(viewer_content)
    return Dataset(xml_path, enrichment_dir, viewer_path=viewer_path)


def validate(dataset: Dataset) -> list[str]:
    """Load the dataset and score it once. Empty list when it is usable."""
    try:
        build_services(dataset).risk.ranking()
    except LOAD_ERRORS as exc:
        return list(getattr(exc, "problems", None) or [str(exc)])
    return []


def discard(dataset: Dataset) -> None:
    """Delete the temporary folder of an uploaded dataset. The reference dataset is never touched."""
    folder = dataset.xml_path.parent
    if not dataset.reference and folder.name.startswith(UPLOAD_PREFIX):
        shutil.rmtree(folder, ignore_errors=True)


def activate(dataset: Dataset) -> None:
    """Make the dataset active for this session. Every other session key refers to the previous products."""
    previous = active_dataset()
    for key in list(st.session_state):
        del st.session_state[key]
    if not dataset.reference:
        st.session_state[DATASET_KEY] = dataset
    if previous != dataset:
        discard(previous)


def load_upload(
    xml_name: str, xml_content: bytes, csv_contents: dict[str, bytes], viewer_content: bytes | None = None
) -> list[str]:
    """Stage, check and activate an upload. Returns the problems found; nothing changes when there are any."""
    problems = precheck(csv_contents)
    if problems:
        return problems
    dataset = stage(xml_name, xml_content, csv_contents, viewer_content)
    problems = validate(dataset)
    if problems:
        discard(dataset)
        return problems
    activate(dataset)
    return []


def _show_problems(problems: list[str]) -> None:
    shown = problems[:MAX_PROBLEMS_SHOWN]
    more = len(problems) - len(shown)
    lines = "\n".join(f"- {p}" for p in shown) + (f"\n- and {more} more" if more > 0 else "")
    st.error(f"The dataset was not loaded, the active one is kept.\n\n{lines}", icon=":material/error:")


@st.dialog("Change dataset", width="large")
def dialog() -> None:
    current = active_dataset()
    st.markdown(f"Active dataset: **{current.name}**" + (", the reference extract" if current.reference else ""))
    st.caption(
        "Upload another 3DEXPERIENCE XPDMXML extract with the same structure. The enrichment files are optional: "
        "without them, the figures that need them say that the data is missing. Format of the CSV files in "
        "docs/data_contract.md. The 3D view is the HTML viewer exported for this extract: only its data is read, "
        "never its code. The files stay in a temporary folder for this session only."
    )
    xml = st.file_uploader("PLM extract (.xml)", type=["xml"], key="upload-xml")
    columns = st.columns(len(ENRICHMENT_LABELS))
    uploads = {
        file: column.file_uploader(f"{text} (optional)", type=["csv"], key=f"upload-{file}", help=f"Read as {file}.")
        for column, (file, text) in zip(columns, ENRICHMENT_LABELS.items(), strict=True)
    }
    viewer = st.file_uploader(
        "3D view (optional)", type=["html", "htm"], key="upload-viewer", help=f"Read as {VIEWER_FILE}."
    )
    with st.container(horizontal=True):
        load = st.button("Load this dataset", type="primary", disabled=xml is None, icon=":material/upload:")
        reset = st.button("Back to the reference dataset", disabled=current.reference, icon=":material/restore:")
    if reset:
        activate(REFERENCE)
        st.rerun()
    if load and xml is not None:
        with st.spinner("Checking the files..."):
            problems = load_upload(
                xml.name,
                xml.getvalue(),
                {file: upload.getvalue() for file, upload in uploads.items() if upload is not None},
                viewer.getvalue() if viewer is not None else None,
            )
        if not problems:
            st.rerun()
        _show_problems(problems)
