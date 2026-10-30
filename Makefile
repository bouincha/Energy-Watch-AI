.PHONY: install run test lint eval docker-build docker-run

install:
	pip install -r requirements-dev.txt

run:
	uvicorn app.main:app --reload --port 8000

test:
	pytest --cov=app --cov-report=term-missing

lint:
	ruff check app tests

eval:
	python -m app.evaluation.run_eval --json reports/eval.json

docker-build:
	docker build -t energywatch-ai:local .

docker-run:
	docker run --rm -p 8000:8000 --env-file .env energywatch-ai:local
