# COSBELLE RAG Chat 통합 서버

이 프로젝트는 기존 UI(`app/`)와 RBAC RAG 패키지(`dataschool-3rd-project-team3/rbac_rag`)를 하나의 FastAPI 서버에서 실행합니다.

## 실행 구조

- 단일 entrypoint: `app.main:app`
- UI: `http://127.0.0.1:3000`
- RAG JSON API: `POST /v1/chat`
- RAG SSE API: `POST /v1/chat/stream`
- UI 호환 API: `POST /api/chat`, `POST /api/admin/simulate`
- UI 호환 SSE API: `POST /api/chat/stream`, `POST /api/admin/simulate/stream`

별도 `8000` RAG API 서버는 실행하지 않아도 됩니다.

## 로컬 실행

```powershell
cd C:\project3
python -m pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 3000
```

브라우저:

- 공개 채팅: `http://127.0.0.1:3000`
- 관리자 화면: `http://127.0.0.1:3000/admin`

## 환경 변수

Databricks 연결에는 아래 값이 필요합니다.

- `DATABRICKS_HOST`
- `DATABRICKS_SERVER_HOSTNAME`
- `DATABRICKS_WAREHOUSE_ID` or `DATABRICKS_HTTP_PATH`
- `DATABRICKS_CLIENT_ID`
- `DATABRICKS_CLIENT_SECRET`

Databricks Apps 운영에서는 `DATABRICKS_CLIENT_ID`, `DATABRICKS_CLIENT_SECRET`가 앱 런타임에 주입됩니다. `app.yaml`은 SQL warehouse resource key `sql-warehouse`를 `DATABRICKS_WAREHOUSE_ID`로 연결합니다. GitHub에는 `.env`, PAT, OAuth secret, token 값을 올리지 않습니다.

로컬 개발에서만 `DATABRICKS_TOKEN` PAT fallback을 사용할 수 있습니다. 토큰 값은 터미널, 로그, 문서에 출력하지 않습니다.

Guard profile:

- `RBAC_RAG_GUARD_PROFILE=notebook_demo`: 발표/데모용. Databricks Notebook 흐름처럼 domain 기반 RBAC와 SQL 실행 후 Post-check를 사용합니다.
- `RBAC_RAG_GUARD_PROFILE=strict`: 운영 강화형. role table allowlist, SQL column validation, sensitive table guard, answer guard를 적용합니다.

현재 `app.yaml`은 노트북 데모 재현을 위해 `notebook_demo`로 설정되어 있습니다.

## 테스트

```powershell
cd C:\project3
python -m pytest -q
```

현재 통합 테스트는 fake RAG service를 사용해 단일 FastAPI 앱의 `/v1/*`, `/api/*`, SSE 응답 형태를 검증합니다.
