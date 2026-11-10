# BESS SCADA & Protection Design Agent

AI-assisted design tool for BESS projects. **Phase 1 (this release): SLD reading & verification.**

| Phase | Feature | Status |
|---|---|---|
| 1 | Login → upload SLD → AI reads SLD → rule checks → editable summary → engineer confirmation | ✅ |
| 2 | Build SCADA Network (architecture, network drawings, IP/VLAN plan, I/O list) | Planned |
| 3 | Design Protection System (IEC 60909 short circuit, relay/ANSI selection, CT sizing, coordination) | Planned |

## How it works

1. **Login** – email checked against the allowlist in Streamlit secrets (optional shared access code).
2. **Upload** – PDF, PNG/JPG/TIFF or DXF. Vector PDF/DXF text is extracted directly (most reliable);
   large sheets are split into overlapping zoom tiles so small ratings remain legible.
3. **AI reading** – Claude (vision) fills a fixed JSON schema (`core/schema.py`). Rules: no
   guessing, `null` for anything not visible, evidence + confidence on every row.
4. **Engineering checks** (`core/checks.py`, plain code, no AI) – topology, voltage consistency,
   transformer rated currents (IEC 60076-1), breaker rated current (IEC 62271-1), CT primary vs
   load (IEC 61869-2), capacity/duration, metering (CEA Metering Regulations). Every derived value
   shows formula + reference; judgement-based rules are labelled *Engineering practice*.
5. **Engineer review** – correct the equipment/bus tables, re-run checks, then confirm.
   Download JSON/Excel. The confirmed JSON is the input for Phases 2 and 3.

## Deploy on Streamlit Community Cloud

1. Push this folder to a GitHub repo (keep it **private** if possible).
2. share.streamlit.io → **Create app** → select repo, branch, main file `app.py`.
3. **Advanced settings → Secrets** → paste the contents of `.streamlit/secrets.toml.example`
   with your real Anthropic API key and the allowed email IDs.
4. Deploy. To add/remove users later, edit the secrets – no code change or redeploy needed.

## Run locally

```bash
pip install -r requirements.txt
cp .streamlit/secrets.toml.example .streamlit/secrets.toml   # then edit it
streamlit run app.py
python tests/test_checks.py
```

## Notes & limits

* `secrets.toml` is git-ignored – never commit API keys.
* Email-only login identifies users but is not strong authentication: anyone who knows an
  allowed email can sign in. Set `access_code`, or move to Streamlit's OIDC login
  (`st.login` with Google/Microsoft) before wider rollout. Failed-attempt lockout is per
  browser session.
* For DWG drawings, export to DXF (AutoCAD: `SAVEAS` → DXF) before upload.
* AI reading accuracy depends on drawing quality – the confirmation step is mandatory by design.
* Output is a draft for review by a qualified engineer, not a sign-off.
