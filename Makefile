# Pharma AI OS — developer commands
PY      := .venv/bin/python
API     := MONGODB_DB=pharmacy_ai_os

.PHONY: help backend frontend demo test lint up down clean zip

help:
	@echo "make backend   - run API with master seed            (http://localhost:8000)"
	@echo "make demo      - run API with master + demo dataset  (rich UI data)"
	@echo "make frontend  - run Vite dev server                 (http://localhost:5173)"
	@echo "make test      - backend workflow/E2E test suite"
	@echo "make up        - docker compose up (mongo+redis+api+web)"
	@echo "make down      - docker compose down"
	@echo "make zip       - build production ZIP deliverable"

backend:
	cd backend && $(API) ../$(PY) -m uvicorn app.main:app --port 8000 --reload

demo:
	cd backend && $(API) SEED_DEMO=1 ../$(PY) -m uvicorn app.main:app --port 8000 --reload

frontend:
	cd frontend && npm run dev

test:
	cd backend && ../$(PY) -m pytest tests/ -q

up:
	docker compose up --build -d
	@echo "UI: http://localhost:8080   API: http://localhost:8000/docs"

down:
	docker compose down

clean:
	find . -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true
	rm -rf frontend/dist 2>/dev/null || true

zip:
	bash scripts/make_zip.sh
