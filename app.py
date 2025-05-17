import os
import re
import tempfile
import pandas as pd
import cantools

import dash
from dash import dcc, html, Input, Output, State, dash_table, ctx
import plotly.graph_objs as go
from flask import send_file

UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# ========== CAN DECODING FUNCTION ==========
def decode_can(dbc_path, trc_path):
    db = cantools.database.load_file(dbc_path)
    with open(trc_path, 'r') as file:
        header = [next(file) for _ in range(10)]
    version_line = next((line for line in header if ";$FILEVERSION=" in line), "")
    file_version = version_line.strip().split("=")[-1]

    pattern_v1 = re.compile(r'^\s*\d+\)\s+(\d+\.\d+)\s+\w+\s+([0-9A-Fa-f]+)\s+(\d+)\s+([0-9A-Fa-f\s]+)')
    pattern_v2 = re.compile(r'\s*\d+\s+(\d+\.\d+)\s+DT\s+\d+\s+([0-9A-Fa-f]+)\s+Rx\s+(\d+)\s+([0-9A-Fa-f\s]+)')
    pattern_v3 = re.compile(r'^\s*\d+\s+(\d+\.\d+)\s+DT\s+\d+\s+([0-9A-Fa-f]+)\s+Rx\s+-\s+(\d+)\s+([0-9A-Fa-f\s]+)')
    if file_version == "1.1":
        pattern = pattern_v1
    elif file_version == "2.0":
        pattern = pattern_v2
    elif file_version == "2.1":
        pattern = pattern_v3
    else:
        pattern = pattern_v3

    timestamps, message_ids, message_payloads = [], [], []
    with open(trc_path, 'r') as file:
        for line in file:
            match = pattern.match(line)
            if match:
                timestamps.append(float(match.group(1)))
                message_ids.append(int(match.group(2), 16))
                message_payloads.append(match.group(4).strip())

    # Prepare dataframe
    result = {'Timestamp': timestamps}
    for message in db.messages:
        for s in message.signals:
            result.setdefault(s.name, [None] * len(timestamps))

    # Decode signals
    for idx, (msg_id, payload) in enumerate(zip(message_ids, message_payloads)):
        try:
            msg = db.get_message_by_frame_id(msg_id)
        except KeyError:
            continue
        data_bytes = bytes(int(x, 16) for x in payload.split())
        decoded = msg.decode(data_bytes, decode_choices=False, scaling=True)
        for k, v in decoded.items():
            result[k][idx] = v

    df = pd.DataFrame(result)
    # Remove columns where all values are None except Timestamp
    df = df.dropna(axis=1, how='all')
    return df

# ========== DASH APP ==========
app = dash.Dash(__name__)
server = app.server

app.layout = html.Div([
    html.H2("CAN DBC+TRC Decoder"),
    dcc.Upload(
        id='upload-dbc',
        children=html.Button('Upload DBC'),
        multiple=False
    ),
    dcc.Upload(
        id='upload-trc',
        children=html.Button('Upload TRC'),
        multiple=False
    ),
    html.Div(id='file-status', style={'margin': '10px'}),
    html.Button("Decode and Plot", id="decode-btn"),
    html.Div(id='plot-div'),
    html.Br(),
    html.Button("Download CSV", id="download-csv-btn", n_clicks=0, disabled=True),
    dcc.Download(id="download-csv"),
])

# ========== CALLBACK STATE ==========
dbc_tmp_path = os.path.join(UPLOAD_FOLDER, "latest.dbc")
trc_tmp_path = os.path.join(UPLOAD_FOLDER, "latest.trc")
decoded_csv_path = os.path.join(UPLOAD_FOLDER, "decoded.csv")

# ========== CALLBACKS ==========
@app.callback(
    Output('file-status', 'children'),
    Input('upload-dbc', 'contents'),
    State('upload-dbc', 'filename'),
    Input('upload-trc', 'contents'),
    State('upload-trc', 'filename'),
    prevent_initial_call=True
)
def save_files(dbc_content, dbc_name, trc_content, trc_name):
    msg = []
    if dbc_content and dbc_name:
        content_string = dbc_content.split(',')[1]
        with open(dbc_tmp_path, "wb") as f:
            f.write(base64.b64decode(content_string))
        msg.append(f"DBC uploaded: {dbc_name}")
    if trc_content and trc_name:
        content_string = trc_content.split(',')[1]
        with open(trc_tmp_path, "wb") as f:
            f.write(base64.b64decode(content_string))
        msg.append(f"TRC uploaded: {trc_name}")
    if msg:
        return " | ".join(msg)
    return ""

@app.callback(
    [Output('plot-div', 'children'),
     Output('download-csv-btn', 'disabled')],
    Input('decode-btn', 'n_clicks'),
    prevent_initial_call=True
)
def decode_and_plot(n_clicks):
    if not (os.path.exists(dbc_tmp_path) and os.path.exists(trc_tmp_path)):
        return "Please upload both files.", True
    df = decode_can(dbc_tmp_path, trc_tmp_path)
    if len(df) == 0 or len(df.columns) < 2:
        return "No data to plot.", True
    # Save CSV for download
    df.to_csv(decoded_csv_path, index=False)
    # Create plots
    figs = []
    signal_cols = [c for c in df.columns if c.lower() != "timestamp"]
    for col in signal_cols:
        figs.append(dcc.Graph(
            figure=go.Figure(
                data=[go.Scatter(x=df["Timestamp"], y=df[col], mode="lines+markers", name=col)],
                layout=go.Layout(title=col, xaxis_title="Time (ms)", yaxis_title=col)
            )
        ))
    return figs, False

@app.callback(
    Output("download-csv", "data"),
    Input("download-csv-btn", "n_clicks"),
    prevent_initial_call=True
)
def download_csv(n):
    if os.path.exists(decoded_csv_path):
        return dcc.send_file(decoded_csv_path)
    return dash.no_update

# ========== NEEDED IMPORT FOR FILES ==========
import base64

# ========== MAIN ==========
if __name__ == "__main__":
    app.run(port=5050, debug=True)