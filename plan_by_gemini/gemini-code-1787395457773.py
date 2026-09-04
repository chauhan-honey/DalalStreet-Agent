import streamlit as st
import requests
import json

st.set_page_config(page_title="DalalStreet Agent", layout="wide", page_icon="📈")

st.title("📈 DalalStreet-Agent: Indian Financial Multi-Agent Auditor")
st.markdown("Autonomous research system cross-examining Annual Report disclosures against live NSE/BSE fundamentals.")

col_left, col_right = st.columns([1, 2])

with col_left:
    company_choice = st.selectbox(
        "Select Target Company",
        ["Tata Consultancy Services", "Reliance Industries", "Infosys"]
    )
    ticker_map = {
        "Tata Consultancy Services": "TCS.NS",
        "Reliance Industries": "RELIANCE.NS",
        "Infosys": "INFY.NS"
    }
    ticker = ticker_map[company_choice]

    query = st.text_area(
        "Audit / Research Directive",
        value="Evaluate management disclosures regarding cloud margin stability and cross-examine with operating margins and cash flows."
    )
    run_button = st.button("Start Multi-Agent Audit", type="primary", use_container_width=True)

with col_right:
    status_box = st.status("Workflow Execution", expanded=True)
    report_placeholder = st.empty()

if run_button:
    status_box.write("⚙️ Initializing StateGraph...")
    
    url = "http://localhost:8000/api/v1/research/stream"
    payload = {"company_name": company_choice, "ticker": ticker, "user_query": query}
    
    try:
        with requests.post(url, json=payload, stream=True, timeout=120) as resp:
            for line in resp.iter_lines():
                if line:
                    decoded = line.decode("utf-8")
                    if decoded.startswith("data: "):
                        event = json.loads(decoded.replace("data: ", ""))
                        node = event["node"]
                        
                        if node == "planner":
                            status_box.write("📋 **Planner:** Decomposition & sub-queries generated.")
                        elif node == "rag_worker":
                            status_box.write("📄 **RAG Worker:** Extracted PDF disclosures with page citations.")
                        elif node == "market_worker":
                            status_box.write("📈 **Market Worker (MCP):** Retrieved live NSE/BSE fundamentals.")
                        elif node == "critic":
                            verdict = event["data"].get("critic_verdict")
                            status_box.write(f"🔍 **Critic Node:** Evaluation complete. Verdict: `{verdict}`")
                        elif node == "synthesizer":
                            status_box.update(label="Audit Complete", state="complete", expanded=False)
                            report_placeholder.markdown(event["data"]["final_report"])
    except Exception as e:
        st.error(f"Execution error: {str(e)}")