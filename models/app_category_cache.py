from datetime import datetime
from extensions import db


class AppCategoryCache(db.Model):
    """
    Persistent cache of application-name → category mappings.

    Populated at ingestion time by services/app_classifier.py.
    Report queries read from this table — they never trigger Claude API calls.

    source: 'hardcoded' | 'claude'
    """
    __tablename__ = 'app_category_cache'

    app_name   = db.Column(db.String(200), primary_key=True)
    category   = db.Column(db.String(50), nullable=False)
    source     = db.Column(db.String(20), nullable=False, default='claude')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
