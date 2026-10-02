"""Interfaz local de conversación para explorar una base de datos."""

from __future__ import annotations

import streamlit as st

from ui.backend import (IntegrationUnavailable, append_message, artifact_path, ask,
                        create_backend, init_session, load_report_tools,
                        reset_conversation)

st.set_page_config(page_title="Conversación con tus datos", page_icon="💬", layout="wide")
init_session(st.session_state)

st.title("💬 Conversa con tus datos")
st.caption("Aplicación local para explorar la base con preguntas en lenguaje natural.")

with st.sidebar:
    st.subheader("Conversación")
    if st.button("Nueva conversación", use_container_width=True, disabled=st.session_state.get("busy", False)):
        reset_conversation(st.session_state)
        st.rerun()
    st.info("La ejecución está desactivada al preparar cada consulta. Podrás confirmarla después de revisar el plan.")

if st.session_state.backend is None:
    try:
        st.session_state.backend = create_backend()
    except IntegrationUnavailable as exc:
        st.warning(str(exc))

suggestions = [
    "¿Qué tablas y temas puedo explorar?",
    "Resume la información disponible en la base.",
    "¿Qué datos faltan o parecen incompletos?",
]
st.write("**Puedes empezar con:**")
cols = st.columns(len(suggestions))
suggested = None
for column, text in zip(cols, suggestions):
    if column.button(text, use_container_width=True, disabled=st.session_state.get("busy", False)):
        suggested = text


def render_response(response: dict, index: int) -> None:
    status = response["status"]
    icons = {"planned": "📝", "clarification": "❓", "completed": "✅", "blocked": "⛔", "error": "⚠️"}
    st.markdown(f"{icons[status]} {response['message']}")
    if response.get("plan") or response.get("sql"):
        with st.expander("Detalles técnicos (plan y SQL)"):
            if response.get("plan"):
                st.write(response["plan"])
            if response.get("sql"):
                st.code(str(response["sql"]), language="sql")
    data = response.get("data")
    if data is not None:
        st.dataframe(data, use_container_width=True)
        if response.get("truncated"):
            st.warning("Se muestra solo una parte de los resultados porque fueron truncados.")
        reports = load_report_tools()
        if reports is None:
            st.caption("Exportaciones y gráficas no disponibles en esta instalación.")
        else:
            with st.expander("Exportar o crear una gráfica"):
                fmt = st.selectbox("Formato", ["csv", "xlsx"], key=f"fmt-{index}")
                if st.button("Preparar descarga", key=f"export-{index}"):
                    try:
                        # The title is controlled by the app and rooted in this session's temp dir.
                        title = str(artifact_path(st.session_state, "resultados"))
                        artifact = reports.export(data, fmt, title)
                        payload = artifact if isinstance(artifact, bytes) else __import__("pathlib").Path(artifact).read_bytes()
                        st.download_button("Descargar", payload, file_name=f"resultados.{fmt}", key=f"dl-{index}")
                    except Exception:
                        st.error("No se pudo crear el archivo.")
                columns = list(data[0]) if isinstance(data, list) and data and isinstance(data[0], dict) else []
                chart_type = st.selectbox("Tipo de gráfica", ["bar", "line", "scatter"], key=f"chart-type-{index}")
                x = st.selectbox("Columna horizontal", columns, key=f"x-{index}", disabled=not columns)
                y = st.selectbox("Columna de valores", columns, key=f"y-{index}", disabled=not columns)
                if st.button("Crear gráfica", key=f"chart-{index}", disabled=not columns):
                    try:
                        st.plotly_chart(reports.chart(data, {"type": chart_type, "x": x, "y": y}), use_container_width=True)
                    except Exception:
                        st.error("No se pudo crear la gráfica con esas columnas.")


for index, message in enumerate(st.session_state.messages):
    with st.chat_message(message["role"]):
        if message["role"] == "assistant" and message.get("response"):
            render_response(message["response"], index)
        else:
            st.markdown(message["content"])

pending = st.session_state.pending_execution
if pending and pending["token"] not in st.session_state.executed_tokens:
    if st.button("Ejecutar consulta validada", type="primary", disabled=st.session_state.backend is None):
        # Consume before calling: a rerun can never repeat this execution.
        st.session_state.executed_tokens.add(pending["token"])
        st.session_state.pending_execution = None
        response = ask(st.session_state.backend, pending["question"], pending["history"], execute=True)
        append_message(st.session_state, {"role": "assistant", "content": response["message"], "response": response})
        st.rerun()

question = st.chat_input("Escribe una pregunta sobre tus datos", disabled=st.session_state.backend is None)
question = suggested or question
if question:
    previous = list(st.session_state.messages)
    append_message(st.session_state, {"role": "user", "content": question})
    response = ask(st.session_state.backend, question, previous, execute=False)
    append_message(st.session_state, {"role": "assistant", "content": response["message"], "response": response})
    if response["status"] == "planned":
        st.session_state.pending_execution = {"question": question, "history": previous, "token": len(st.session_state.messages)}
    st.rerun()
