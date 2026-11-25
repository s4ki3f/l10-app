"""Write the OpenAPI document (the frontend generates its TypeScript types from it).  python -m app.openapi_dump out.json"""
import json
import sys

from .main import app

json.dump(app.openapi(), open(sys.argv[1], "w", encoding="utf-8"), indent=1)
