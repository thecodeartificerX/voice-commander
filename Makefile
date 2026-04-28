# Makefile — Voice Commander task runner
# Python tasks use `python -m …`; Node/pnpm tasks are in web/builder-ui/

.PHONY: builder-install builder-dev builder-build builder-test builder-e2e builder-lint

builder-install:
	cd web/builder-ui && pnpm install --frozen-lockfile

builder-dev:
	cd web/builder-ui && pnpm dev

builder-build:
	cd web/builder-ui && pnpm build

builder-test:
	cd web/builder-ui && pnpm test --run

builder-e2e:
	cd web/builder-ui && pnpm test:e2e

builder-lint:
	cd web/builder-ui && pnpm lint && pnpm typecheck
