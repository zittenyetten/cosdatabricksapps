# 로컬 개발, 테스트, 배포 가이드

## 현재 로컬 실행 가능 범위

현재 저장소는 Databricks 노트북에서 분리된 패키지입니다. 실제 UI와 함께 실행할 때는 루트 프로젝트 `C:\project3`의 단일 FastAPI 앱에서 import해서 사용합니다.

로컬에서 가능한 것:

- Python 문법 검증
- 순수 함수/클래스 단위 테스트 추가
- fake 객체를 사용한 라우터/엔진 테스트
- 루트 통합 FastAPI 서버의 API/SSE 테스트

로컬에서 바로 어려운 것:

- 실제 Spark SQL 실행
- `dbutils.widgets` 기반 입력
- Databricks Model Serving 호출
- Databricks AI Search index 호출
- `cos_adb` Unity Catalog 테이블 조회

## 기본 환경

권장 Python 버전:

- Python 3.11

현재 확인된 로컬 Python:

```powershell
python -c "import sys; print(sys.version)"
```

문법 검증:

```powershell
cd C:\project3\dataschool-3rd-project-team3
python -m compileall .
```

## 로컬 가상환경

```powershell
cd C:\project3
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
```

통합 서버 의존성은 루트 `requirements.txt`에 정의되어 있으며, 이 RAG 패키지는 editable package로 설치됩니다.

Databricks Connect를 선택하면 `databricks-connect`가 추가됩니다. 단, Databricks Runtime 버전과 Python 버전 호환성을 맞춰야 합니다.

## 환경 변수 초안

로컬 API 서버에서 Databricks를 직접 호출하려면 최소한 아래 값이 필요합니다.

```powershell
$env:DATABRICKS_HOST = "https://<workspace-host>"
$env:DATABRICKS_SERVER_HOSTNAME = "<workspace-host-without-scheme>"
$env:DATABRICKS_WAREHOUSE_ID = "<warehouse-id>"
$env:DATABRICKS_CLIENT_ID = "<app-or-service-principal-client-id>"
$env:RBAC_RAG_LLM_MODEL = "databricks-qwen3-next-80b-a3b-instruct"
$env:RBAC_RAG_VS_INDEX_NAME = "cos_adb.search.metadata_chunks_index"
$env:RBAC_RAG_CATALOG = "cos_adb"
```

`DATABRICKS_CLIENT_SECRET`은 터미널 출력/문서/커밋에 남기지 말고 OS 환경변수, 로컬 `.env`, 또는 Databricks Apps secret으로만 설정합니다. 운영 배포에서는 개인 PAT를 쓰지 않고 Databricks Apps가 주입하는 App OAuth credential을 사용합니다. 로컬 개발에서만 `DATABRICKS_TOKEN` PAT fallback을 사용할 수 있습니다.

## 로컬 테스트 전략

### 1. 단위 테스트

Databricks 연결 없이 검증할 수 있는 대상:

- `QueryRouter`
- `ConversationMemory`
- `DatabricksLLM.extract_sql`
- `DatabricksLLM.build_context`
- `logging_utils.extract_tables`
- RBAC 도메인 매핑 로직
- response formatter

예상 테스트 예:

```text
tests/
  test_router.py
  test_llm_utils.py
  test_logging_utils.py
  test_rbac.py
```

### 2. Fake adapter 테스트

FastAPI 전환 전에 다음 fake 객체를 만드는 것이 좋습니다.

| Fake | 역할 |
| --- | --- |
| `FakeLLM` | intent 분류, SQL 생성, 요약 결과를 고정값으로 반환 |
| `FakeSearch` | 질문별 메타데이터 검색 결과 반환 |
| `FakeSQLExecutor` | SQL별 pandas DataFrame 또는 row list 반환 |
| `FakeRBACRepository` | role별 허용 도메인 반환 |
| `FakeAuditLogger` | 로그를 메모리 리스트에 저장 |

이렇게 하면 Databricks 없이도 아래 시나리오를 검증할 수 있습니다.

- CHAT 질문은 SQL을 실행하지 않는다.
- WORK 질문은 검색, SQL 생성, 실행, 요약 순서로 진행된다.
- 허용되지 않은 domain은 SQL 실행 전에 차단된다.
- SQL 실행 1회 실패 시 재생성 후 재시도한다.
- Post-check 실패 시 데이터는 반환하지 않는다.
- 로그가 항상 남는다.

### 3. Databricks 통합 테스트

실제 Databricks에 연결하는 테스트는 기본 단위 테스트와 분리해야 합니다.

예:

```powershell
pytest tests/integration -m databricks
```

통합 테스트 최소 확인:

- `cos_adb.governance.access_policies`에서 role별 system 조회 가능
- `cos_adb.search.llm_table_context` 조회 가능
- AI Search index query 가능
- Model Serving endpoint query 가능
- SQL Warehouse에서 `SELECT 1` 가능
- 로그 테이블 append 가능

## 로컬 통합 서버 실행

환경 파일:

```powershell
cd C:\project3
copy .env.example .env
```

`.env` 값을 확인할 때는 토큰이 노출되지 않도록 아래 명령을 사용합니다.

```powershell
python scripts/check_env.py --file .env
```

의존성 설치:

```powershell
python -m pip install -r requirements.txt
```

서버 실행:

```powershell
uvicorn app.main:app --host 127.0.0.1 --port 3000 --reload
```

JSON endpoint 호출:

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri "http://127.0.0.1:3000/v1/chat" `
  -ContentType "application/json" `
  -Body '{"question":"지난 분기 품질 이슈 요약해줘","role_id":"GENERAL_EMPLOYEE","mode":"auto"}'
```

SSE endpoint는 `POST /v1/chat/stream`입니다. 기존 UI는 같은 서버의 `POST /api/chat/stream`, `POST /api/admin/simulate/stream`을 `fetch` streaming으로 소비합니다.

## 테스트

기본 테스트:

```powershell
pytest -q
```

루트 통합 테스트:

```powershell
cd C:\project3
pytest -q
```

Databricks 통합 smoke test:

```powershell
$env:RUN_DATABRICKS_TESTS = "1"
pytest -q -m databricks
```

## 로컬 배포 후보

### Docker 컨테이너

통합 FastAPI 서버를 컨테이너로 만들면 로컬과 운영 배포 차이를 줄일 수 있습니다.

예상 구성:

```text
Dockerfile
requirements.txt
app/
  main.py
rbac_rag/
```

실행:

```powershell
docker build -t rbac-rag-api .
docker run --env-file .env -p 3000:3000 rbac-rag-api
```

### Databricks Apps

Databricks 내부에서 웹/API 앱으로 운영할 경우 후보입니다.

필요 파일:

```text
app.yaml
requirements.txt
app/
  main.py
rbac_rag/
```

장점:

- Databricks 내부 리소스와 가까움
- Unity Catalog/SQL/OAuth 연동에 유리
- 별도 서버 운영 부담 감소

검토할 점:

- 외부 프론트엔드에서 호출할 인증 방식
- SSE/WebSocket 지원과 timeout
- app compute 비용과 리소스 제한
- 배포 자동화 방식

## 다음 구현 작업

로컬 테스트/배포를 실제로 가능하게 하려면 아래 순서로 진행하는 것이 좋습니다.

1. API 인증 기반 role 결정 구조 추가
2. Databricks OAuth M2M 또는 service principal 인증 적용
3. Dockerfile 또는 Databricks Apps 배포 파일 추가
4. 프론트엔드 fetch streaming 연동
5. 운영 모니터링과 timeout/retry 정책 추가

## 참고 공식 문서

- Databricks Connect: https://docs.databricks.com/aws/en/dev-tools/databricks-connect/
- Databricks SQL Connector for Python: https://docs.databricks.com/aws/en/dev-tools/python-sql-connector
- Databricks Apps: https://docs.databricks.com/aws/en/dev-tools/databricks-apps/
- Databricks Apps 배포: https://docs.databricks.com/aws/en/dev-tools/databricks-apps/deploy
