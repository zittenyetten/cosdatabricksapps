from rbac_rag import create_app
from rbac_rag.runner import run_and_exit_notebook


app = create_app(spark=spark, dbutils=dbutils)
run_and_exit_notebook(app, dbutils)