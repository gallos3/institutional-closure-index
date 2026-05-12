## Measuring Institutional Closure in Public Procurement

This repository contains the code and data structures used in the paper:

**"Measuring Institutional Closure in Public Procurement: A Network-Based Index from European Buyer–Supplier Data"**

The project introduces the **Institutional Closure Index (ICI)**, a network-based indicator designed to quantify the structural configuration of supplier concentration and relational persistence in public procurement systems.
The ICI is computed at the authority level within CPV-defined domains and time windows, combining supplier concentration with relational persistence.
The repository also includes validation and correlation plots corresponding to the analyses reported in Appendix A.4.
---

### 🚀 Overview

The codebase provides a complete pipeline for:

- **Data Extraction:** Querying a Neo4j graph database of procurement records (TED) to compute authority–supplier network features  
- **Metric Calculation:** Computing HHI, relational indicators, and the Institutional Closure Index (ICI) across count-weighted and value-weighted variants  
- **Analysis:** Generating the summary statistics, typologies (quadrants), and figures reported in the paper  

---

### 📂 Repository Structure

- `featuresall.py` — Data extraction and feature computation  script (computes dyadic and authority-level metrics including HHI, HF, AA, and PA) from Neo4j. Set your Neo4j credentials via environment variables.
- `build_panel_and_summary.py` — Aggregation into authority-level panel dataset  
- `make_tables_and_figures2.py` — Generation of tables and figures  
- `validation_stress.py`: Performs sensitivity analysis and stress tests (counterfactuals) for index validation.
- `run_batches.py`: Python orchestrator for batch processing (recommended for cross-platform use).
- `run_all.ps1` — Batch execution across CPV categories and time windows  
- `correl_plots.py`: Diagnostic script for correlation analysis between index variants.
- `requirements.txt` — Python dependencies  

---
### 🔗 Data & Citation

The processed dataset (authority_panel.parquet) is available via Zenodo.

A citable archived version of this repository will be deposited on Zenodo upon publication.

---
### 🔗 Repository

The code is publicly available at: https://github.com/gallos3/institutional-closure-index

---

### 🛠️ Installation & Setup

```bash
git clone https://github.com/gallos3/institutional-closure-index.git
cd institutional-closure-index
pip install -r requirements.txt
``