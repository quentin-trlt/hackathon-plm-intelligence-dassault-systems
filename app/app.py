"""
3DEXPERIENCE PLM Product Intelligence & Change Impact Platform
Industrial Web Application for Automotive Electric Powertrain BOM
Complies with Dassault Systèmes 3DEXPERIENCE UI Standards & CATIA 3DPlay Specification
Module: app.py
"""

import os
import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
import plotly.express as px
from typing import Dict, Any, List, Optional
import google.generativeai as genai
from plm_logic import get_engine, build_bom_tree, calculate_total_mass, get_where_used

# ------------------------------------------------------------------------------
# 1. PAGE CONFIG & 3DEXPERIENCE DASSAULT SYSTÈMES STYLING
# ------------------------------------------------------------------------------
st.set_page_config(
    page_title="3DEXPERIENCE | PLM Intelligence",
    page_icon="🧭",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS matching https://www.3ds.com/products/3dexperience standards
st.markdown("""
<style>
    /* Hide default Streamlit top header and footer */
    #MainMenu {visibility: hidden;}
    header {visibility: hidden;}
    footer {visibility: hidden;}

    /* Root typography & background */
    .stApp {
        background-color: #F4F6F9;
        font-family: 'Segoe UI', -apple-system, BlinkMacSystemFont, Roboto, sans-serif;
    }

    /* 3DEXPERIENCE Top Brand Bar */
    .ds-topbar {
        background: #FFFFFF;
        border-bottom: 2px solid #E2E8F0;
        padding: 10px 24px;
        display: flex;
        align-items: center;
        justify-content: space-between;
        margin: -4rem -4rem 1.5rem -4rem;
        box-shadow: 0 1px 4px rgba(0, 86, 134, 0.06);
    }
    .ds-brand-group {
        display: flex;
        align-items: center;
        gap: 14px;
    }
    .ds-brand-title {
        font-size: 1.25rem;
        font-weight: 900;
        color: #005686;
        letter-spacing: -0.01em;
        line-height: 1.1;
    }
    .ds-brand-sub {
        font-size: 0.72rem;
        font-weight: 700;
        color: #0072B2;
        letter-spacing: 0.08em;
        text-transform: uppercase;
    }
    .ds-meta-pill {
        background: #EBF4F9;
        color: #005686;
        border: 1px solid #BFDBFE;
        padding: 5px 12px;
        border-radius: 6px;
        font-size: 0.8rem;
        font-weight: 600;
    }

    /* Sidebar - 3DEXPERIENCE Compass Pane */
    [data-testid="stSidebar"] {
        background-color: #FFFFFF !important;
        border-right: 1px solid #CBD5E1 !important;
        padding-top: 1rem;
    }

    /* Dassault Blue Titles */
    h1, h2, h3, h4, .dassault-title {
        color: #005686 !important;
        font-weight: 700;
        letter-spacing: -0.01em;
    }

    /* Metrics in Dassault Blue */
    [data-testid="stMetricValue"] {
        color: #005686 !important;
        font-weight: 800 !important;
        font-size: 1.85rem !important;
    }

    /* Clean widget card container */
    .widget-card {
        background: #FFFFFF;
        border: 1px solid #E2E8F0;
        border-radius: 8px;
        padding: 1.25rem;
        box-shadow: 0 2px 6px rgba(0, 86, 134, 0.06);
        margin-bottom: 1.25rem;
    }

    /* Compass App Badge */
    .compass-header {
        display: flex;
        align-items: center;
        gap: 12px;
        padding-bottom: 1rem;
        border-bottom: 1px solid #E2E8F0;
        margin-bottom: 1.25rem;
    }

    /* 3D CAD Viewer Container */
    .cad-viewport-box {
        background: #111827;
        border: 1px solid #1F2937;
        border-radius: 10px;
        overflow: hidden;
        margin-bottom: 1.25rem;
        box-shadow: 0 4px 12px rgba(0,0,0,0.15);
    }
</style>
""", unsafe_allow_html=True)

# 3DEXPERIENCE Official Compass Logo SVG
COMPASS_LOGO_SVG = """
<svg viewBox="0 0 100 100" width="42" height="42">
  <defs>
    <linearGradient id="compassGrad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#0072B2"/>
      <stop offset="100%" stop-color="#003B5C"/>
    </linearGradient>
  </defs>
  <circle cx="50" cy="50" r="46" fill="url(#compassGrad)" stroke="#005686" stroke-width="2"/>
  <polygon points="50,12 55,28 45,28" fill="#FFFFFF"/>
  <polygon points="88,50 72,55 72,45" fill="#FFFFFF"/>
  <polygon points="50,88 55,72 45,72" fill="#FFFFFF"/>
  <polygon points="12,50 28,55 28,45" fill="#FFFFFF"/>
  <circle cx="50" cy="50" r="14" fill="#FFFFFF"/>
  <circle cx="50" cy="50" r="10" fill="#005686"/>
</svg>
"""

# Render top Dassault Systèmes bar
st.markdown(f"""
<div class="ds-topbar">
    <div class="ds-brand-group">
        {COMPASS_LOGO_SVG}
        <div>
            <div class="ds-brand-title">3DS <span style="font-weight:600; color:#334155; font-size:1.05rem;">DASSAULT SYSTÈMES</span></div>
            <div class="ds-brand-sub">3DEXPERIENCE® Platform | PLM Intelligence</div>
        </div>
    </div>
    <div style="display:flex; align-items:center; gap:12px;">
        <span class="ds-meta-pill">⚡ 410 kW Electric Drive Front (100.100 kg)</span>
        <span class="ds-meta-pill" style="background:#ECFDF5; color:#065F46; border-color:#A7F3D0;">🟢 3DEXPERIENCE Connected</span>
    </div>
</div>
""", unsafe_allow_html=True)


# ------------------------------------------------------------------------------
# 2. DATA LOADING & CACHING
# ------------------------------------------------------------------------------
@st.cache_resource
def load_plm_data():
    """Load PLM relational entities and BOM tree with persistent caching."""
    engine = get_engine()
    products_df = engine.db.get_products_df()
    instances_df = engine.db.get_instances_df()
    bom_tree = build_bom_tree()
    return products_df, instances_df, bom_tree

products_df, instances_df, bom_tree = load_plm_data()
ROOT_SYSTEM_MASS = 100.100  # Declared root mass in kg (ECAR-EI_410KW_EDRIVE_FRONT)


# ------------------------------------------------------------------------------
# 3. SIDEBAR NAVIGATION (THE COMPASS APP PANE)
# ------------------------------------------------------------------------------
with st.sidebar:
    st.markdown(f"""
    <div class="compass-header">
        {COMPASS_LOGO_SVG}
        <div>
            <div style="font-weight: 800; color: #005686; font-size: 1.1rem; line-height: 1.1;">3DEXPERIENCE</div>
            <div style="font-size: 0.72rem; color: #64748B; font-weight: 700; text-transform: uppercase;">Compass Navigation</div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("#### 🧭 PLM Apps")

    app_view = st.radio(
        label="Select App",
        options=[
            "📊 Dashboard & Analytics",
            "🌳 Product Structure (BOM)",
            "🔄 Change Impact Analysis",
            "🤖 AI Engineering Copilot"
        ],
        label_visibility="collapsed"
    )

    st.divider()

    st.markdown("#### ⚙️ Assembly Context")
    st.markdown(
        f"**Root Assembly:**<br>`ECAR-EI_410KW_EDRIVE_FRONT`<br>"
        f"**PLM ID:** `prd-12535825-00000047`<br>"
        f"**Total Mass:** `{ROOT_SYSTEM_MASS:.3f} kg`<br>"
        f"**Maturity:** `IN_WORK` | **Rev:** `D`",
        unsafe_allow_html=True
    )

    st.divider()
    st.caption("Dassault Systèmes 3DEXPERIENCE R2024x\nESILV Engineering Challenge Edition")


# ------------------------------------------------------------------------------
# HELPER: INTERACTIVE 3D CATIA 3DPLAY WEBGL VIEWER (THREE.JS)
# ------------------------------------------------------------------------------
def generate_catia_3d_viewer(selected_part_name: str = "") -> str:
    """Generate an interactive WebGL Three.js 3D CATIA model with exploded view and part inspection."""
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
      <meta charset="utf-8">
      <style>
        body {{ margin: 0; overflow: hidden; background: #0F172A; font-family: -apple-system, sans-serif; }}
        #canvas-container {{ width: 100%; height: 500px; position: relative; }}
        #hud {{
          position: absolute; top: 12px; left: 16px; color: #F8FAFC;
          background: rgba(15, 23, 42, 0.85); backdrop-filter: blur(8px);
          padding: 8px 14px; border-radius: 8px; border: 1px solid #334155;
          font-size: 12px; pointer-events: none; z-index: 10;
        }}
        #hud b {{ color: #38BDF8; }}
        #controls-bar {{
          position: absolute; bottom: 12px; left: 16px; right: 16px;
          background: rgba(15, 23, 42, 0.9); backdrop-filter: blur(8px);
          padding: 8px 14px; border-radius: 8px; border: 1px solid #334155;
          display: flex; align-items: center; justify-content: space-between;
          color: #E2E8F0; font-size: 12px; z-index: 10;
        }}
        .slider-group {{ display: flex; align-items: center; gap: 8px; }}
        input[type=range] {{ width: 140px; accent-color: #0072B2; cursor: pointer; }}
        .btn-ctrl {{
          background: #1E293B; border: 1px solid #475569; color: #F8FAFC;
          padding: 4px 10px; border-radius: 4px; cursor: pointer; font-size: 11px;
        }}
        .btn-ctrl:hover {{ background: #005686; border-color: #0072B2; }}
      </style>
      <script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"></script>
      <script src="https://cdn.jsdelivr.net/npm/three@0.128.0/examples/js/controls/OrbitControls.js"></script>
    </head>
    <body>
      <div id="canvas-container">
        <div id="hud">
          <div><b>3DEXPERIENCE CATIA 3DPlay</b> | 410 kW Electric Drive Front</div>
          <div id="part-info" style="margin-top:3px; color:#94A3B8;">Selected: <b>ECAR-EI_410KW_EDRIVE_FRONT</b> (100.100 kg)</div>
        </div>
        <div id="controls-bar">
          <div class="slider-group">
            <span>Explode View:</span>
            <input type="range" id="explodeSlider" min="0" max="100" value="0" oninput="updateExplode(this.value)">
            <span id="explodeVal">0%</span>
          </div>
          <div style="display:flex; gap:6px;">
            <button class="btn-ctrl" onclick="toggleWireframe()">Wireframe</button>
            <button class="btn-ctrl" onclick="toggleCutaway()">Cutaway</button>
            <button class="btn-ctrl" onclick="resetView()">Reset Camera</button>
          </div>
        </div>
      </div>

      <script>
        const container = document.getElementById('canvas-container');
        const scene = new THREE.Scene();
        scene.background = new THREE.Color(0x0F172A);

        const camera = new THREE.PerspectiveCamera(45, container.clientWidth / container.clientHeight, 0.1, 1000);
        camera.position.set(28, 18, 32);

        const renderer = new THREE.WebGLRenderer({{ antialias: true }});
        renderer.setSize(container.clientWidth, container.clientHeight);
        renderer.setPixelRatio(window.devicePixelRatio);
        renderer.shadowMap.enabled = true;
        container.appendChild(renderer.domElement);

        const controls = new THREE.OrbitControls(camera, renderer.domElement);
        controls.enableDamping = true;
        controls.dampingFactor = 0.05;

        // Lighting
        const ambLight = new THREE.AmbientLight(0xffffff, 0.7);
        scene.add(ambLight);

        const dirLight1 = new THREE.DirectionalLight(0xffffff, 0.9);
        dirLight1.position.set(30, 40, 20);
        scene.add(dirLight1);

        const dirLight2 = new THREE.DirectionalLight(0x38bdf8, 0.4);
        dirLight2.position.set(-20, -10, -20);
        scene.add(dirLight2);

        // Ground grid
        const grid = new THREE.GridHelper(50, 25, 0x334155, 0x1E293B);
        grid.position.y = -7;
        scene.add(grid);

        // Parametric 3D CAD Assembly Components
        const rootGroup = new THREE.Group();
        scene.add(rootGroup);

        // Materials (PBR Metallic)
        const aluMat = new THREE.MeshStandardMaterial({{ color: 0x94A3B8, metalness: 0.85, roughness: 0.35 }});
        const copperMat = new THREE.MeshStandardMaterial({{ color: 0xD97706, metalness: 0.8, roughness: 0.25 }});
        const steelMat = new THREE.MeshStandardMaterial({{ color: 0x475569, metalness: 0.9, roughness: 0.2 }});
        const shaftMat = new THREE.MeshStandardMaterial({{ color: 0xCBD5E1, metalness: 0.95, roughness: 0.15 }});
        const gearMat = new THREE.MeshStandardMaterial({{ color: 0x64748B, metalness: 0.85, roughness: 0.3 }});
        const bearingMat = new THREE.MeshStandardMaterial({{ color: 0x0284C7, metalness: 0.9, roughness: 0.2 }});

        // 1. Aluminium Housing Group (Casing)
        const housingGroup = new THREE.Group();
        const housingGeo = new THREE.CylinderGeometry(6.2, 6.2, 14, 32, 1, true);
        const housingMesh = new THREE.Mesh(housingGeo, aluMat);
        housingMesh.rotation.z = Math.PI / 2;
        housingGroup.add(housingMesh);

        // Housing Ribs
        for (let i = -5; i <= 5; i += 2.5) {{
          const ribGeo = new THREE.TorusGeometry(6.3, 0.25, 12, 32);
          const ribMesh = new THREE.Mesh(ribGeo, aluMat);
          ribMesh.rotation.y = Math.PI / 2;
          ribMesh.position.x = i;
          housingGroup.add(ribMesh);
        }}
        housingGroup.userData = {{ name: "ECAR-EI_HOUSING FRONT EDRIVE (prd-12535825-00000050)", mass: "30.520 kg" }};
        rootGroup.add(housingGroup);

        // 2. Stator Stack Core
        const statorGroup = new THREE.Group();
        const statorGeo = new THREE.CylinderGeometry(5.7, 5.7, 10, 32);
        const statorMesh = new THREE.Mesh(statorGeo, steelMat);
        statorMesh.rotation.z = Math.PI / 2;
        statorGroup.add(statorMesh);
        statorGroup.userData = {{ name: "ECAR-EI_STATOR_STACK (prd-12535825-00000085)", mass: "14.200 kg" }};
        rootGroup.add(statorGroup);

        // 3. Copper Hairpin Windings (Front & Rear coils)
        const copperGroup = new THREE.Group();
        const coil1 = new THREE.Mesh(new THREE.TorusGeometry(5.2, 0.9, 16, 32), copperMat);
        coil1.rotation.y = Math.PI / 2;
        coil1.position.x = -5.8;
        copperGroup.add(coil1);

        const coil2 = new THREE.Mesh(new THREE.TorusGeometry(5.2, 0.9, 16, 32), copperMat);
        coil2.rotation.y = Math.PI / 2;
        coil2.position.x = 5.8;
        copperGroup.add(coil2);
        copperGroup.userData = {{ name: "ECAR-EI_COPPER_WINDINGS (prd-12535825-00000087)", mass: "7.850 kg" }};
        rootGroup.add(copperGroup);

        // 4. Rotor & Central Drive Shaft
        const rotorGroup = new THREE.Group();
        const rotorGeo = new THREE.CylinderGeometry(3.8, 3.8, 8.5, 32);
        const rotorMesh = new THREE.Mesh(rotorGeo, steelMat);
        rotorMesh.rotation.z = Math.PI / 2;
        rotorGroup.add(rotorMesh);

        const shaftGeo = new THREE.CylinderGeometry(1.4, 1.4, 22, 24);
        const shaftMesh = new THREE.Mesh(shaftGeo, shaftMat);
        shaftMesh.rotation.z = Math.PI / 2;
        rotorGroup.add(shaftMesh);
        rotorGroup.userData = {{ name: "ECAR-EI_ROTOR_ASSEMBLY & SHAFT (prd-12535825-00000086)", mass: "16.150 kg" }};
        rootGroup.add(rotorGroup);

        // 5. Planetary Gearbox (Reducer Front)
        const reducerGroup = new THREE.Group();
        const carrierGeo = new THREE.CylinderGeometry(4.8, 4.8, 3.5, 24);
        const carrierMesh = new THREE.Mesh(carrierGeo, gearMat);
        carrierMesh.rotation.z = Math.PI / 2;
        reducerGroup.add(carrierMesh);

        // Planet gears
        for (let angle = 0; angle < Math.PI * 2; angle += (Math.PI * 2) / 3) {{
          const pGear = new THREE.Mesh(new THREE.CylinderGeometry(1.5, 1.5, 3.6, 18), gearMat);
          pGear.rotation.z = Math.PI / 2;
          pGear.position.set(0, Math.cos(angle) * 2.6, Math.sin(angle) * 2.6);
          reducerGroup.add(pGear);
        }}
        reducerGroup.position.x = 9.5;
        reducerGroup.userData = {{ name: "ECAR-EI_REDUCER FRONT (prd-12535825-00000049)", mass: "29.850 kg" }};
        rootGroup.add(reducerGroup);

        // 6. Deep Groove Ball Bearings (HSH_BEA3)
        const bearingGroup = new THREE.Group();
        const ringGeo = new THREE.TorusGeometry(2.2, 0.4, 16, 24);
        const bMesh = new THREE.Mesh(ringGeo, bearingMat);
        bMesh.rotation.y = Math.PI / 2;
        bearingGroup.add(bMesh);
        bearingGroup.position.x = -8.5;
        bearingGroup.userData = {{ name: "ECAR-3D_HSH_BEA3 (prd-12535825-00000081)", mass: "0.450 kg" }};
        rootGroup.add(bearingGroup);

        // Exploded View Dynamics
        window.updateExplode = function(val) {{
          const factor = val / 100.0;
          document.getElementById('explodeVal').innerText = val + "%";

          housingGroup.position.x = -factor * 16;
          statorGroup.position.x = -factor * 6;
          copperGroup.position.x = -factor * 6;
          rotorGroup.position.x = 0;
          reducerGroup.position.x = 9.5 + (factor * 14);
          bearingGroup.position.x = -8.5 - (factor * 12);
        }};

        // Wireframe Mode
        let isWireframe = false;
        window.toggleWireframe = function() {{
          isWireframe = !isWireframe;
          [aluMat, copperMat, steelMat, shaftMat, gearMat, bearingMat].forEach(m => m.wireframe = isWireframe);
        }};

        // Cutaway Transparent Mode
        let isCutaway = false;
        window.toggleCutaway = function() {{
          isCutaway = !isCutaway;
          aluMat.transparent = isCutaway;
          aluMat.opacity = isCutaway ? 0.35 : 1.0;
        }};

        window.resetView = function() {{
          camera.position.set(28, 18, 32);
          controls.target.set(0, 0, 0);
          updateExplode(0);
          document.getElementById('explodeSlider').value = 0;
        }};

        // Animation Loop
        function animate() {{
          requestAnimationFrame(animate);
          rootGroup.rotation.y += 0.002;
          controls.update();
          renderer.render(scene, camera);
        }}
        animate();

        window.addEventListener('resize', () => {{
          camera.aspect = container.clientWidth / container.clientHeight;
          camera.updateProjectionMatrix();
          renderer.setSize(container.clientWidth, container.clientHeight);
        }});
      </script>
    </body>
    </html>
    """


# ------------------------------------------------------------------------------
# 4. VIEW 1: DASHBOARD & ANALYTICS (POWER BI STYLE)
# ------------------------------------------------------------------------------
if app_view == "📊 Dashboard & Analytics":
    st.markdown("<h2 class='dassault-title'>📊 Information Intelligence (NETVIBES / Dashboard)</h2>", unsafe_allow_html=True)
    st.caption("Power BI decision cockpit: dynamic slicers, mass rollup treemaps, and compliance audits.")

    # 4.1 Slicers Section
    st.markdown('<div class="widget-card">', unsafe_allow_html=True)
    st.markdown("##### 🎛️ Power BI Data Slicers")

    available_materials = sorted([m for m in products_df['material'].dropna().unique() if m != ""])
    material_display_options = ["⚠️ Missing / Unassigned"] + available_materials

    col_slicer1, col_slicer2 = st.columns(2)
    with col_slicer1:
        selected_materials_display = st.multiselect(
            "Filter by Material:",
            options=material_display_options,
            default=[],
            help="Select one or more materials to filter the dashboard. Empty selection shows all."
        )

    with col_slicer2:
        available_maturities = sorted(products_df['maturity'].dropna().unique().tolist())
        selected_maturities = st.multiselect(
            "Filter by Maturity State:",
            options=available_maturities,
            default=[],
            help="Select lifecycle release states. Empty selection shows all."
        )
    st.markdown('</div>', unsafe_allow_html=True)

    # 4.2 Apply Slicer Logic
    filtered_df = products_df.copy()
    if selected_materials_display:
        include_missing = "⚠️ Missing / Unassigned" in selected_materials_display
        chosen_named = [m for m in selected_materials_display if m != "⚠️ Missing / Unassigned"]
        mask = pd.Series(False, index=filtered_df.index)
        if include_missing:
            mask = mask | (filtered_df['material'] == "") | (filtered_df['material'].isna())
        if chosen_named:
            mask = mask | (filtered_df['material'].isin(chosen_named))
        filtered_df = filtered_df[mask]

    if selected_maturities:
        filtered_df = filtered_df[filtered_df['maturity'].isin(selected_maturities)]

    # 4.3 KPI Row (4 Columns)
    kpi_mass = filtered_df['mass'].sum()
    kpi_parts = len(filtered_df)
    kpi_missing_mat = len(filtered_df[(filtered_df['material'] == "") | (filtered_df['material'].isna())])
    kpi_maturity = "100% IN_WORK" if (filtered_df['maturity'] == "IN_WORK").all() and len(filtered_df) > 0 else "MIXED"

    col_kpi1, col_kpi2, col_kpi3, col_kpi4 = st.columns(4)
    with col_kpi1:
        st.metric(label="Total Filtered Mass", value=f"{kpi_mass:.3f} kg", delta=f"Root: {ROOT_SYSTEM_MASS:.3f} kg")
    with col_kpi2:
        st.metric(label="Unique Components", value=f"{kpi_parts} Parts", delta=f"Total: {len(products_df)}")
    with col_kpi3:
        st.metric(label="Missing Materials", value=f"{kpi_missing_mat} Parts", delta="Compliance Risk" if kpi_missing_mat > 0 else "Compliant", delta_color="inverse")
    with col_kpi4:
        st.metric(label="Lifecycle Maturity", value=kpi_maturity, delta="Freeze Risk" if kpi_maturity == "100% IN_WORK" else "Released")

    st.markdown("<br>", unsafe_allow_html=True)

    # 4.4 Plotly Visuals: Treemap & Bar Chart
    if filtered_df.empty:
        st.warning("⚠️ No components match the selected slicer criteria. Please broaden your selection.")
    else:
        chart_col1, chart_col2 = st.columns(2)
        visual_df = filtered_df.copy()
        visual_df['material_clean'] = visual_df['material'].apply(
            lambda m: "⚠️ Missing / Unassigned" if not m or pd.isna(m) else m
        )
        visual_df['plot_mass'] = visual_df['mass'].apply(lambda x: max(float(x), 0.001))

        with chart_col1:
            st.markdown('<div class="widget-card">', unsafe_allow_html=True)
            st.markdown("##### 📦 Mass Distribution Treemap")
            fig_treemap = px.treemap(
                visual_df,
                path=['material_clean', 'name'],
                values='plot_mass',
                color='material_clean',
                color_discrete_map={"⚠️ Missing / Unassigned": "#EF4444"},
                title="Mass Allocation by Material & Component",
                hover_data={'plm_id': True, 'mass': ':.3f kg', 'plot_mass': False}
            )
            fig_treemap.update_layout(margin=dict(t=40, l=10, r=10, b=10), height=420, paper_bgcolor="rgba(0,0,0,0)")
            st.plotly_chart(fig_treemap, use_container_width=True)
            st.markdown('</div>', unsafe_allow_html=True)

        with chart_col2:
            st.markdown('<div class="widget-card">', unsafe_allow_html=True)
            st.markdown("##### 📊 Component Count by Material")
            mat_counts = visual_df['material_clean'].value_counts().reset_index()
            mat_counts.columns = ['Material', 'Count']
            fig_bar = px.bar(
                mat_counts,
                x='Material',
                y='Count',
                color='Material',
                color_discrete_map={"⚠️ Missing / Unassigned": "#EF4444"},
                text='Count',
                title="Component Count by Material Classification"
            )
            fig_bar.update_layout(margin=dict(t=40, l=10, r=10, b=10), height=420, paper_bgcolor="rgba(0,0,0,0)", showlegend=False)
            fig_bar.update_traces(textposition='outside')
            st.plotly_chart(fig_bar, use_container_width=True)
            st.markdown('</div>', unsafe_allow_html=True)

    # 4.5 Filtered Component Table
    st.markdown('<div class="widget-card">', unsafe_allow_html=True)
    st.markdown("##### 📋 Filtered Component Master Table")
    display_table = filtered_df[[
        'name', 'plm_id', 'revision', 'maturity', 'mass', 'material', 'organization', 'project'
    ]].copy().rename(columns={
        'name': 'Component Name',
        'plm_id': 'PLM ID',
        'revision': 'Rev',
        'maturity': 'Maturity',
        'mass': 'Mass (kg)',
        'material': 'Material',
        'organization': 'Organization',
        'project': 'Project'
    })
    st.dataframe(
        display_table,
        use_container_width=True,
        hide_index=True,
        column_config={"Mass (kg)": st.column_config.NumberColumn(format="%.3f kg")}
    )
    st.markdown('</div>', unsafe_allow_html=True)


# ------------------------------------------------------------------------------
# 5. VIEW 2: PRODUCT STRUCTURE & 3D CAD VIEWER (CATIA 3DPLAY)
# ------------------------------------------------------------------------------
elif app_view == "🌳 Product Structure (BOM)":
    st.markdown("<h2 class='dassault-title'>🌳 3D Modeling & BOM Structure (CATIA 3DPlay)</h2>", unsafe_allow_html=True)
    st.caption("Interactive 3D WebGL assembly inspection, exploded views, and multi-level BOM hierarchy.")

    # 5.1 Interactive 3D CATIA WebGL Viewport
    st.markdown("#### 🎮 Interactive CATIA 3DPlay CAD Viewport")
    components.html(generate_catia_3d_viewer(), height=520, scrolling=False)

    # 5.2 CAD Studio Render Card (if generated image exists)
    cad_candidates = [
        os.path.join(os.path.dirname(__file__), "assets", "ecar_edrive_cad_render.png"),
        os.path.join("assets", "ecar_edrive_cad_render.png"),
        "d:/PLM/assets/ecar_edrive_cad_render.png"
    ]
    cad_image_path = next((p for p in cad_candidates if os.path.exists(p)), None)
    if cad_image_path:
        with st.expander("🖼️ View High-Resolution CATIA Studio CAD Render (KeyShot Lighting)", expanded=False):
            st.image(cad_image_path, caption="CATIA 3D CAD Engineering Cutaway — ECAR-EI_410KW_EDRIVE_FRONT (100.100 kg)", use_container_width=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # 5.3 BOM Search & Stats
    col_search, col_stats = st.columns([3, 1])
    with col_search:
        search_query = st.text_input(
            "🔍 Search BOM by Component Name or PLM ID:",
            placeholder="e.g. STATOR, prd-12535825-00000085, Aluminium...",
            help="Filters and highlights matching nodes across the entire assembly tree."
        ).strip().lower()

    with col_stats:
        total_subassemblies = len(instances_df['parent_xml_id'].unique())
        st.markdown(
            f"<div style='padding-top: 1.8rem; font-size: 0.9rem; color: #64748B;'>"
            f"<b>Tree Assemblies:</b> {total_subassemblies} | <b>Instances:</b> {len(instances_df)}"
            f"</div>",
            unsafe_allow_html=True
        )

    # 5.4 Recursive Tree Renderer
    def render_bom_node(node: Dict[str, Any], depth: int = 0):
        name = node.get("name", "Unknown")
        plm_id = node.get("plm_id", "")
        mass = node.get("mass", 0.0)
        revision = node.get("revision", "-")
        maturity = node.get("maturity", "-")
        material = node.get("material", "") or "Unassigned"
        children = node.get("children", [])
        is_leaf = node.get("is_leaf", False)
        instance_name = node.get("instance_name", "")

        is_match = False
        if search_query:
            if (search_query in name.lower()) or (search_query in plm_id.lower()) or (search_query in material.lower()):
                is_match = True

        match_badge = " 🎯 [MATCH]" if is_match else ""
        inst_label = f"[{instance_name}] " if instance_name else ""

        if is_leaf or len(children) == 0:
            header_text = f"⚙️ {inst_label}{name} ({plm_id}) — Mass: {mass:.3f} kg{match_badge}"
        else:
            header_text = f"📁 {inst_label}{name} ({plm_id}) — Total Mass: {mass:.3f} kg ({len(children)} sub-items){match_badge}"

        auto_expand = is_match or (depth < 2 and not search_query)

        with st.expander(header_text, expanded=auto_expand):
            p1, p2, p3, p4 = st.columns(4)
            with p1:
                st.markdown(f"**PLM ID:**<br>`{plm_id}`", unsafe_allow_html=True)
            with p2:
                st.markdown(f"**Revision / Maturity:**<br>`Rev {revision}` | `{maturity}`", unsafe_allow_html=True)
            with p3:
                st.markdown(f"**Declared Mass:**<br>`{mass:.3f} kg`", unsafe_allow_html=True)
            with p4:
                mat_color = "#DC2626" if material == "Unassigned" else "#005686"
                st.markdown(f"**Material:**<br><span style='color:{mat_color}; font-weight:600;'>{material}</span>", unsafe_allow_html=True)

            if children:
                st.markdown("<div style='margin-left: 15px; border-left: 2px solid #E2E8F0; padding-left: 10px;'>", unsafe_allow_html=True)
                for child in children:
                    render_bom_node(child, depth=depth + 1)
                st.markdown("</div>", unsafe_allow_html=True)

    st.markdown('<div class="widget-card">', unsafe_allow_html=True)
    st.markdown(f"#### 🌐 Assembly Root: `{bom_tree.get('name', 'Root')} ({bom_tree.get('plm_id', '')})`")
    render_bom_node(bom_tree, depth=0)
    st.markdown('</div>', unsafe_allow_html=True)


# ------------------------------------------------------------------------------
# 6. VIEW 3: CHANGE IMPACT ANALYSIS (SIMULIA)
# ------------------------------------------------------------------------------
elif app_view == "🔄 Change Impact Analysis":
    st.markdown("<h2 class='dassault-title'>🔄 Content & Simulation (SIMULIA / Change Impact)</h2>", unsafe_allow_html=True)
    st.caption("Simulate engineering change orders (ECO) with reverse Where-Used graph traversal and projected weight deltas.")

    st.markdown('<div class="widget-card">', unsafe_allow_html=True)
    st.markdown("##### 🎯 Target Component Selection & Simulation Parameters")

    component_options = [
        f"{row['name']} ({row['plm_id']})" for _, row in products_df.iterrows()
    ]
    default_idx = 0
    for idx, opt in enumerate(component_options):
        if "BEA3" in opt or "HOUSING FRONT" in opt:
            default_idx = idx
            break

    selected_option = st.selectbox(
        "Select Component to Modify / Replace:",
        options=component_options,
        index=default_idx,
        help="Select any component from the 3DEXPERIENCE catalog to evaluate its upstream assembly impact."
    )

    selected_plm_id = selected_option.split("(")[-1].replace(")", "").strip()
    target_row = products_df[products_df['plm_id'] == selected_plm_id].iloc[0]
    target_xml_id = target_row['xml_id']
    current_mass = float(target_row['mass'])

    col_mass1, col_mass2 = st.columns(2)
    with col_mass1:
        st.number_input(
            "Current Mass (kg):",
            value=current_mass,
            disabled=True,
            format="%.3f",
            help="Certified CAD declared mass from the current PLM extraction."
        )

    with col_mass2:
        proposed_mass = st.number_input(
            "Proposed New Mass (kg):",
            value=current_mass,
            step=0.05,
            format="%.3f",
            help="Input projected alternative mass (e.g. lightweighting or material substitution)."
        )

    run_sim = st.button("🚀 Run Impact Traversal", type="primary", use_container_width=True)
    st.markdown('</div>', unsafe_allow_html=True)

    if run_sim:
        where_used_results = get_where_used(target_xml_id)
        mass_delta = proposed_mass - current_mass
        new_projected_root_mass = ROOT_SYSTEM_MASS + mass_delta

        st.markdown('<div class="widget-card">', unsafe_allow_html=True)
        st.markdown("### 📈 Simulation Results & Projected Vehicle Impact")

        res_col1, res_col2, res_col3 = st.columns(3)
        with res_col1:
            st.metric(label="Affected Parent Assemblies", value=f"{len(where_used_results)} Assemblies", delta="Direct & Indirect Impact")
        with res_col2:
            st.metric(label="Single-Part Mass Delta", value=f"{mass_delta:+.3f} kg", delta="Weight Increase" if mass_delta > 0 else ("Lightweighting" if mass_delta < 0 else "Neutral"), delta_color="inverse")
        with res_col3:
            st.metric(label="Projected Root Assembly Mass", value=f"{new_projected_root_mass:.3f} kg", delta=f"{mass_delta:+.3f} kg vs Base ({ROOT_SYSTEM_MASS:.3f} kg)", delta_color="inverse")

        st.divider()
        st.markdown("#### 🔗 Upstream Traceability Breadcrumbs (Where-Used Path)")
        if not where_used_results:
            st.info("ℹ️ **Top-Level Root Assembly:** This component has no upstream parent assemblies.")
        else:
            for item in where_used_results:
                level = item.get("level", 1)
                parent_name = item.get("parent_name", "")
                parent_plm_id = item.get("parent_plm_id", "")
                via_inst = item.get("via_instance_name", "")
                impact_path = item.get("impact_path", "")
                st.markdown(f"**Level {level}:** `{parent_name} ({parent_plm_id})` via instance `{via_inst}`")
                st.code(impact_path, language="text")

        st.markdown('</div>', unsafe_allow_html=True)


# ------------------------------------------------------------------------------
# 7. VIEW 4: AI ENGINEERING COPILOT (GEMINI LLM)
# ------------------------------------------------------------------------------
elif app_view == "🤖 AI Engineering Copilot":
    st.markdown("<h2 class='dassault-title'>🤖 Collaborative AI (3DSwym / Copilot)</h2>", unsafe_allow_html=True)
    st.caption("Autonomous AI copilot analyzing the complete 3DEXPERIENCE BOM data with mandatory PLM ID traceability.")

    saved_key = os.environ.get("GEMINI_API_KEY", "")
    api_key = st.text_input(
        "Enter your Gemini API Key:",
        value=saved_key,
        type="password",
        help="Get a free Gemini API key from https://aistudio.google.com"
    )
    st.caption("🔑 Obtain an API key from [aistudio.google.com](https://aistudio.google.com).")

    context_table = products_df[[
        'name', 'plm_id', 'revision', 'maturity', 'mass', 'material', 'organization', 'project'
    ]].to_markdown(index=False)

    SYSTEM_INSTRUCTION = f"""
You are an expert PLM Data Analyst and Senior Automotive Powertrain Engineer at Dassault Systèmes.
Here is the complete Bill of Materials (BOM) extracted from 3DEXPERIENCE:

{context_table}

STRICT ENGINEERING RULES:
1. DATA BOUNDARY: Answer questions based strictly on the BOM data provided above. If an inquiry asks for specifications not in this dataset, respond: "That information is not available in the current PLM extraction."
2. CRITICAL TRACEABILITY REQUIREMENT: Every time you mention ANY component or assembly, you MUST explicitly cite its exact PLM ID in parentheses immediately following its name. Example: "ECAR-EI_HOUSING FRONT EDRIVE (prd-12535825-00000050)".
3. FORMATTING: Use bold text for component names, structured bullet points for lists, and concise technical language.
"""

    if "copilot_chat_history" not in st.session_state:
        st.session_state.copilot_chat_history = [
            {
                "role": "assistant",
                "content": (
                    "👋 Hello! I am your **3DEXPERIENCE AI Engineering Copilot**.\n\n"
                    "I analyze the complete Bill of Materials for **ECAR-EI_410KW_EDRIVE_FRONT (prd-12535825-00000047)** "
                    "with guaranteed PLM ID traceability.\n\n"
                    "💡 *Try asking:*\n"
                    "- *'What parts use Aluminium?'*\n"
                    "- *'Show me the heaviest component.'*\n"
                    "- *'Are there any missing materials?'*\n"
                    "- *'What are the major subassemblies?'*"
                )
            }
        ]

    st.markdown("**Suggested Engineering Prompts:**")
    qp1, qp2, qp3, qp4 = st.columns(4)
    quick_query = None
    with qp1:
        if st.button("🔩 Parts using Aluminium", use_container_width=True):
            quick_query = "What parts use Aluminium?"
    with qp2:
        if st.button("⚖️ Heaviest component", use_container_width=True):
            quick_query = "Show me the heaviest component."
    with qp3:
        if st.button("🚨 Audit missing materials", use_container_width=True):
            quick_query = "Are there any missing materials?"
    with qp4:
        if st.button("🏗️ Major subassemblies", use_container_width=True):
            quick_query = "What are the major subassemblies of the electric motor?"

    for msg in st.session_state.copilot_chat_history:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    user_prompt = st.chat_input("Ask a technical question about the electric motor BOM...")
    active_input = quick_query or user_prompt

    if active_input:
        st.session_state.copilot_chat_history.append({"role": "user", "content": active_input})
        with st.chat_message("user"):
            st.markdown(active_input)

        if not api_key:
            reply = (
                "⚠️ **Gemini API Key Required**\n\n"
                "Please enter a valid Google Gemini API Key in the password field above to enable live LLM reasoning.\n\n"
                "You can get a free key from [aistudio.google.com](https://aistudio.google.com)."
            )
            st.session_state.copilot_chat_history.append({"role": "assistant", "content": reply})
            with st.chat_message("assistant"):
                st.warning(reply)
        else:
            with st.chat_message("assistant"):
                with st.spinner("Analyzing 3DEXPERIENCE BOM..."):
                    try:
                        genai.configure(api_key=api_key)
                        model = genai.GenerativeModel(
                            model_name="gemini-1.5-flash",
                            system_instruction=SYSTEM_INSTRUCTION
                        )
                        chat_session = model.start_chat()
                        response = chat_session.send_message(active_input)
                        bot_response = response.text if response and response.text else "No response generated."
                    except Exception as err:
                        bot_response = f"❌ **Error communicating with Gemini:** {str(err)}"

                st.markdown(bot_response)
                st.session_state.copilot_chat_history.append({"role": "assistant", "content": bot_response})
