#!/usr/bin/python3
from flask import Flask, render_template, jsonify, send_from_directory
import json
import os

# /web is the folder that this file lives in
WEB_DIR = os.path.dirname(__file__)
BASE_DIR = os.path.abspath(os.path.join(WEB_DIR, ".."))

app = Flask(
    __name__,
    template_folder=os.path.join(WEB_DIR, "templates"),
    static_folder=os.path.join(WEB_DIR, "static")
)

# JSON flight logs (stored outside /web)
CLOSEST_FILE = os.path.join(BASE_DIR, "close.txt")
FARTHEST_FILE = os.path.join(BASE_DIR, "farthest.txt")


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"Could not load {path}: {e}")
        return default


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/closest/json")
def closest_json():
    return jsonify(load_json(CLOSEST_FILE, {}))


@app.get("/farthest/json")
def farthest_json():
    return jsonify(load_json(FARTHEST_FILE, []))


@app.get("/closest")
def closest_page():
    return render_template("closest_map.html")


@app.get("/farthest")
def farthest_page():
    return render_template("farthest_map.html")


# Serve PNG map snapshots from /web/static/maps/
@app.get("/maps/<path:filename>")
def maps(filename):
    maps_dir = os.path.join(WEB_DIR, "static/maps")
    return send_from_directory(maps_dir, filename)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=False)
