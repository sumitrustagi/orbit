import os
from dotenv import load_dotenv

# Load .env before anything else
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

from app import create_app, create_celery

app = create_app(os.environ.get("FLASK_ENV", "production"))
celery = create_celery(app)

if __name__ == "__main__":
    app.run(
        host="127.0.0.1",
        port=int(os.environ.get("APP_PORT", 8080)),
        debug=False
    )
