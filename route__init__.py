# app/routes/setup.py  ← stub (full code in Section 4)
from flask import Blueprint
setup_bp = Blueprint("setup", __name__, template_folder="../templates/setup")

@setup_bp.route("/")
def index():
    return "Setup wizard — coming in Section 4", 200
