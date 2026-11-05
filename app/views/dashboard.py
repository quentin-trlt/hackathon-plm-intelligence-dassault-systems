"""Overview: the motor at a glance, its subassemblies, what needs attention and the most critical parts."""

from __future__ import annotations

from dataclasses import dataclass
from html import escape

import pandas as pd
import streamlit as st

import nav
import ui
from plm.analysis.summary import SubassemblySummary, subassembly_summary
from plm.parser import MATERIAL_PRICES_FILE, PART_SOURCING_FILE
from plm.parser.xml_reader import MASS_TOLERANCE_KG
from services import Services, leaf_frame
from views import explorer

LEVELS = ("high", "medium", "low")


@dataclass(frozen=True)
class Finding:
    level: str  # a risk level, or info when the finding carries none
    text: str
    page: str
    link: str
    team_data: bool


def _bar_row(label: str, share: float | None, tone: str) -> str:
    value = f"{share:.0%}" if share is not None else "Unknown"
    width = (share or 0.0) * 100
    return (
        f'<div class="anat-row"><div class="anat-row-label"><span>{label}</span><span>{value}</span></div>'
        f'<div class="anat-track"><div class="anat-fill is-{tone}" style="width:{width:.1f}%"></div></div></div>'
    )


def _panel(sub: SubassemblySummary, has_prices: bool) -> str:
    counts = sub.count_by_level
    risk = "".join(f'<span><i class="dot is-{lv}"></i>{counts[lv]} {lv}</span>' for lv in LEVELS)
    worst = (
        f'<p class="anat-worst">Most critical: <b>{escape(sub.worst.name)}</b>, {sub.worst.criticality:.0f}/100</p>'
        if sub.worst
        else '<p class="anat-worst">No scored part below.</p>'
    )
    return (
        f'<p class="anat-name">{escape(sub.name)}</p>'
        f'<p class="anat-mass">{sub.mass_kg:.2f} kg</p>'
        + _bar_row("Share of motor mass", sub.mass_share, "plm")
        + (_bar_row("Share of material cost", sub.cost_share, "team") if has_prices else "")
        + f'<div class="anat-risk" aria-label="Supply risk of the {sub.parts} scored parts below">{risk}'
        f"<span>of {sub.parts} scored</span></div>{worst}"
    )


def _anatomy(s: Services) -> None:
    subs = sorted(subassembly_summary(s.plm, s.cost, s.risk), key=lambda sub: -sub.mass_share)
    root = s.plm.root()
    st.subheader("The motor by subassembly", anchor=False)
    if not subs:
        st.info(f"{root.name} has no subassembly in the extract.")
        return
    st.html(
        f'<p class="anat-intro">{len(subs)} subassemblies under {escape(root.name)}. Each panel is as wide as its '
        "share of the declared mass, and its top edge shows the worst risk level below it.</p>"
    )
    columns = st.columns([max(sub.mass_share, 0.2) for sub in subs], gap="small")
    for index, (column, sub) in enumerate(zip(columns, subs, strict=True)):
        level = sub.worst.level if sub.worst else "none"
        with column, st.container(border=True, key=f"anat-{level}-{index}"):
            st.html(_panel(sub, s.has_prices))
            if st.button("Open in the structure", key=f"open-{sub.plm_id}", icon=":material/account_tree:"):
                explorer.focus(s, sub.plm_id)
                st.switch_page(nav.page("structure"))
    ui.show_sources([ref for sub in subs for ref in sub.sources], "Where the subassembly figures come from")


def _figures(s: Services, parts: pd.DataFrame) -> None:
    root = s.plm.root()
    ranking = s.risk.ranking()
    counts = {lv: sum(1 for r in ranking if r.level == lv) for lv in LEVELS}
    single = int((parts["Suppliers"] == 1).sum())
    cost_complete = parts["Cost in motor (EUR)"].notna().all()
    scored = len(ranking) or 1
    cost = (
        ui.Figure(
            "Material cost",
            f"{parts['Cost in motor (EUR)'].sum():.2f} EUR" if cost_complete else "Incomplete",
            "Mass x material price. Raw material only, not the purchase price.",
            team_data=True,
        )
        if s.has_prices
        else ui.missing_figure("Material cost", MATERIAL_PRICES_FILE)
    )
    sourcing = (
        ui.Figure(
            "Single-sourced parts",
            f"{single} of {len(parts)}",
            "One supplier recorded.",
            team_data=True,
            alert=single > 0,
            bar=(ui.Segment(single / len(parts), "team", f"{single} single-sourced"),),
        )
        if s.has_sourcing
        else ui.missing_figure("Single-sourced parts", PART_SOURCING_FILE)
    )
    ui.figures(
        [
            ui.Figure(
                "Mass of the leaf parts",
                f"{parts['Mass in motor (kg)'].sum():.2f} kg",
                f"{len(parts)} leaf parts, {int(parts['Qty in motor'].sum())} units. Declared {root.mass_kg} kg.",
            ),
            cost,
            ui.Figure(
                "High supply risk",
                f"{counts['high']} parts",
                f"Out of {len(ranking)} scored parts." + ("" if s.has_sourcing else " PLM criteria only."),
                alert=counts["high"] > 0,
                bar=tuple(ui.Segment(counts[lv] / scored, lv, f"{counts[lv]} {lv}") for lv in LEVELS),
            ),
            sourcing,
        ]
    )


def _findings(s: Services, parts: pd.DataFrame) -> list[Finding]:
    findings = []
    missing = s.enrichment.missing_files()
    if missing:
        findings.append(
            Finding(
                "info",
                f"Team data not provided with this dataset: {', '.join(missing)}. The figures that need it say so.",
                "",
                "",
                True,
            )
        )
    ranking = s.risk.ranking()
    if ranking:
        top = ranking[0]
        findings.append(
            Finding(
                top.level,
                f"{top.name} is the most critical part, {top.criticality:.0f}/100 "
                f"(probability {top.probability:.2f}, impact {top.impact:.2f}).",
                "risks",
                "Risks",
                False,
            )
        )
    exposure = [e for e in s.risk.supplier_exposure() if e.parts]
    if exposure and exposure[0].single_sourced_cost_share > 0:
        e = exposure[0]
        findings.append(
            Finding(
                "info",
                f"{e.name} alone supplies {len(e.single_sourced_parts)} part(s) holding "
                f"{e.single_sourced_cost_share:.0%} of the material cost.",
                "supply",
                "Supply",
                True,
            )
        )
    by_material = parts.groupby("Material")[["Mass in motor (kg)", "Cost in motor (EUR)"]].sum(min_count=1)
    mass_share = by_material["Mass in motor (kg)"] / by_material["Mass in motor (kg)"].sum()
    cost_share = by_material["Cost in motor (EUR)"] / by_material["Cost in motor (EUR)"].sum()
    gap = (cost_share - mass_share).dropna()
    if not gap.empty:
        material = gap.idxmax()
        findings.append(
            Finding(
                "info",
                f"{material} is {mass_share[material]:.0%} of the mass but {cost_share[material]:.0%} of the "
                "material cost.",
                "supply",
                "Supply",
                True,
            )
        )
    price = [r for r in s.risk.price_ranking() if r.price_level == "high"]
    if price:
        findings.append(
            Finding(
                "high",
                f"{len(price)} part(s) carry a high price risk, led by {price[0].name} ({price[0].price_risk:.0f}/100).",
                "risks",
                "Risks",
                True,
            )
        )
    reused = [r for r in ranking if len(s.plm.where_used(r.plm_id)) > 1]
    if reused:
        findings.append(
            Finding(
                "info",
                f"{len(reused)} part(s) are used in several places: a defect spreads to each of them.",
                "risks",
                "Risks",
                False,
            )
        )
    return findings


def _attention(s: Services, parts: pd.DataFrame) -> None:
    st.subheader("Needs attention", anchor=False)
    for index, finding in enumerate(_findings(s, parts)):
        with st.container(key=f"att-{index}"):
            text, action = st.columns([5, 1], vertical_alignment="center", gap="small")
            swatch = '&nbsp;<i class="plm-swatch is-team" title="Based on team data"></i>' if finding.team_data else ""
            text.html(
                f'<p class="att-text"><i class="dot is-{finding.level}" title="{finding.level}"></i>'
                f"<span>{escape(finding.text)}{swatch}</span></p>"
            )
            if finding.page:
                with action:
                    nav.link(finding.page, finding.link, icon=":material/arrow_outward:")


def _top_parts(s: Services) -> None:
    st.subheader("Most critical parts", anchor=False)
    items = []
    for r in s.risk.ranking()[:6]:
        material = s.plm.product(r.plm_id).material or ""
        items.append(
            f'<li><span class="crit-name">{escape(r.name)}<small>{escape(material)}</small></span>'
            f'<span class="crit-track is-{r.level}"><span style="width:{r.criticality:.0f}%"></span></span>'
            f'<span class="crit-value">{r.criticality:.0f}</span></li>'
        )
    st.html(f'<ul class="crit-list" aria-label="Criticality out of 100">{"".join(items)}</ul>')
    nav.link("risks", "See the full ranking and the heat map", icon=":material/crisis_alert:")


def _data_quality(s: Services) -> None:
    with st.expander("Data quality", icon=":material/fact_check:"):
        products = s.plm.products()
        leaves = s.plm.leaves()
        root = s.plm.root()
        report = s.enrichment.coverage(s.plm)
        missing = s.enrichment.missing_files()
        excluded = sorted(s.risk.excluded)
        rolled = sum(p.mass_kg * s.plm.total_quantity(p.plm_id).value for p in leaves)
        mass_gap = rolled - root.mass_kg
        single_revision = len({p.revision_family for p in products}) == len(products)
        in_work = sum(1 for p in products if p.maturity == "IN_WORK")
        reps_consistent = sum(
            1
            for p in leaves
            if (rep := s.plm.representation(p.plm_id)) is not None
            and rep.revision == p.revision
            and rep.maturity == p.maturity
        )
        checks = [
            ("Structure", "Single root, no broken reference, no cycle, unique identifiers", "passed at load"),
            (
                "Mass roll-up",
                f"Leaves x quantities = {rolled:.2f} kg vs root {root.mass_kg} kg",
                "consistent" if abs(mass_gap) <= MASS_TOLERANCE_KG else f"gap of {mass_gap:+.3f} kg",
            ),
            (
                "Load warnings",
                f"{len(s.plm.warnings)} non-blocking anomaly",
                "none" if not s.plm.warnings else "shown at the top of the page",
            ),
            (
                "3D representations",
                f"{reps_consistent}/{len(leaves)} leaves with a representation of the same revision and maturity",
                "consistent" if reps_consistent == len(leaves) else "check",
            ),
            (
                "Descriptions",
                f"{sum(1 for p in products if not p.description)}/{len(products)} products without description",
                "gap in the extract",
            ),
            (
                "Lifecycle",
                f"{in_work}/{len(products)} products IN_WORK, "
                + ("one revision per product" if single_revision else "several revisions of some products"),
                "no history" if single_revision else "history available",
            ),
        ]
        if excluded:
            checks.append(
                (
                    "Meshing artefacts",
                    f"{len(excluded)} stator parts modelled for meshing "
                    f"({sum(s.plm.product(p).mass_kg for p in excluded):.2f} kg counted in the stator mass)",
                    "excluded from risk",
                )
            )
        if missing:
            checks.append(("Enrichment files", f"Not provided: {', '.join(missing)}", "missing data"))
        if s.viewer is None:
            checks.append(("3D view", f"Cannot be drawn: {s.viewer_problem}", "missing data"))
        else:
            profiled = len(s.viewer.plm_ids - s.viewer.markers)
            checks.append(
                (
                    "3D view",
                    f"{profiled}/{len(s.viewer.plm_ids)} parts drawn from a shape profile, "
                    f"{s.viewer.placed_from_xml}/{len(s.viewer.items)} positions read from the XML",
                    "consistent" if not s.viewer.markers else "check",
                )
            )
        checks.append(
            (
                "Enrichment coverage",
                f"{len(report.materials_without_price)} material without price, "
                f"{len(report.leaves_without_sourcing)} leaf without supplier, "
                f"{len(report.unfilled_rows)} unfilled row",
                "complete" if report.complete else "incomplete",
            )
        )
        st.dataframe(pd.DataFrame(checks, columns=["Check", "Finding", "Status"]), hide_index=True, width="stretch")
        st.caption(
            "The extract holds no product quality data (defects, tests, deviations, tolerances): quality decisions "
            "rest on the quality of the data itself, checked here."
        )


def render(s: Services) -> None:
    ui.page_header(
        "Overview",
        f"{s.plm.root().name} as described by the OEM extract: how its mass, material cost and supply risk "
        "split across subassemblies, and what to look at first.",
    )
    parts = leaf_frame(s)
    _figures(s, parts)
    _anatomy(s)
    left, right = st.columns([3, 2], gap="large")
    with left:
        _attention(s, parts)
    with right:
        _top_parts(s)

    with st.expander(f"All leaf parts ({len(parts)})", icon=":material/table_rows:"):
        st.dataframe(
            parts,
            hide_index=True,
            width="stretch",
            column_config={
                "Unit mass (kg)": st.column_config.NumberColumn(format="%.3f"),
                "Mass in motor (kg)": st.column_config.NumberColumn(format="%.3f"),
                "Unit cost (EUR)": st.column_config.NumberColumn(format="%.2f"),
                "Cost in motor (EUR)": st.column_config.NumberColumn(format="%.2f"),
                "Criticality": st.column_config.NumberColumn(format="%.1f"),
            },
        )
    _data_quality(s)
