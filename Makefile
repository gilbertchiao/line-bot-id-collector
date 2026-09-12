# 開發與部署輔助指令。uv.lock 是相依的唯一來源；
# src/requirements.txt 由 `make build` 產生、不進版控，供 `sam build` 的 python builder 使用。

REQ := src/requirements.txt
export SAM_CLI_TELEMETRY ?= 0

.PHONY: requirements build deploy validate test lint clean

requirements:
	uv export --frozen --no-dev --no-hashes --no-emit-project -o $(REQ)

build: requirements
	sam build --use-container

deploy: build
	sam deploy --no-fail-on-empty-changeset

validate:
	sam validate --lint --region ap-northeast-1

test:
	uv run pytest -q

lint:
	uv run ruff check .
	uv run ruff format --check .

clean:
	rm -rf .aws-sam $(REQ)
