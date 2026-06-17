# Migration Notes

This repository was created from the local integrated Databricks Apps project at `C:\project3`.

## Previous RAG Package Repository

- Path: `C:\project3\dataschool-3rd-project-team3`
- Remote: `https://github.com/hyeeeee-kim/dataschool-3rd-project-team3.git`
- Branch: `main`
- Last known commit before root repo migration: `74ad2a4dffc39d6d204b783bd2d6675318b25ab4`
- Commit message: `feat: bootstrap modular RBAC RAG package and notebook runner`

The previous repository is preserved upstream. This root repository now owns the integrated UI, FastAPI server, and RAG package as a single Databricks Apps deployment unit.

## Secret Policy

Do not commit `.env`, PAT values, OAuth client secrets, Databricks tokens, or runtime logs. Production credentials are provided by Databricks Apps runtime environment variables and app secrets.
