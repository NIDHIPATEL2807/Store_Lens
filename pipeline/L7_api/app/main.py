"""Flask app factory. Run with:  flask --app app.main run  or  python app/main.py"""

import os
from flask import Flask

from database          import init_db
from ingestion         import ingestion_bp
from metrics           import metrics_bp
from funnel            import funnel_bp
from heatmap           import heatmap_bp
from anomalies         import anomalies_bp
from health            import health_bp
from logging_middleware import register as register_logging


def create_app() -> Flask:
    app = Flask(__name__)

    # Register all blueprints
    for bp in (ingestion_bp, metrics_bp, funnel_bp, heatmap_bp, anomalies_bp, health_bp):
        app.register_blueprint(bp)

    register_logging(app)

    with app.app_context():
        init_db()

    return app


app = create_app()

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    app.run(host="0.0.0.0", port=port, debug=os.getenv("DEBUG", "false").lower() == "true")
