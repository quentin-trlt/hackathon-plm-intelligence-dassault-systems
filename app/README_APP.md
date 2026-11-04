# 🧭 3DEXPERIENCE PLM Intelligence Web Application

An enterprise decision cockpit and change impact engine developed for the **410 kW Electric Drive Front Assembly** (`ECAR-EI_410KW_EDRIVE_FRONT`, 100.100 kg), complying with Dassault Systèmes 3DEXPERIENCE UI standards.

---

## 🚀 Key Features

1. **📊 Information Intelligence (NETVIBES / Dashboard & Analytics)**:
   - Power BI-style dynamic slicers (Material & Lifecycle Maturity).
   - Treemap showing mass distribution by material and subassembly.
   - Frequency bar chart highlighting unassigned material compliance risks.
   - Searchable component master table.

2. **🌳 3D Modeling & BOM Structure (CATIA 3DPlay)**:
   - Interactive 3D WebGL / Three.js CATIA CAD viewport with 360° orbital rotation, panning, and zoom.
   - Axial Exploded View slider (0% to 100%) to inspect internal components.
   - Wireframe mode and Cutaway translucent casing mode.
   - Multi-level recursive BOM tree with search filtering and CAD property cards.

3. **🔄 Content & Simulation (SIMULIA / Change Impact Analysis)**:
   - Component substitution simulation with certified CAD declared mass.
   - Upstream Where-Used reverse graph traversal identifying all affected parent assemblies up to the root.
   - Live projected total assembly mass calculation and traceability breadcrumbs.

4. **🤖 Collaborative AI (3DSwym / Engineering Copilot)**:
   - Live LLM reasoning powered by Gemini analyzing the full 3DEXPERIENCE BOM context.
   - Strict engineering prompt rules enforcing explicit PLM ID citations (`prd-12535825-...`) for every cited component.
   - Interactive chat interface with one-click quick prompt pills.

---

## ⚡ How to Run

### Option 1: Streamlit Dashboard (Recommended)
From the repository root or inside this `app` directory:

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Launch the Streamlit application
streamlit run app.py
```

The application will open in your browser at `http://localhost:8501`.

### Option 2: Standalone 3DEXPERIENCE Web App
Open `index.html` directly in any modern web browser or host statically (e.g. Netlify / Vercel / GitHub Pages). It includes the full 3D WebGL CATIA viewport, interactive slicers, charts, and offline Copilot reasoning engine.

---

## 📂 App Structure

```text
app/
├── app.py                         # Unified Streamlit Web Application (4 views, 3DEXPERIENCE styling)
├── plm_logic.py                   # Hierarchical graph engine (BOM DFS, Where-Used BFS, Mass rollups)
├── plm_parser.py                  # XML parser & relational SQLite engine
├── XPDMXML_ECAR_PLM.xml           # 3DEXPERIENCE PLM dataset (79 products, 112 instances)
├── index.html                     # Standalone 3DEXPERIENCE Web Cockpit with Three.js 3DPlay
├── requirements.txt               # Application dependencies
├── assets/
│   └── ecar_edrive_cad_render.png # CATIA Studio 3D CAD cutaway render
└── README_APP.md                  # Documentation (this file)
```
