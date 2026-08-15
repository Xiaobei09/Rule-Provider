# Rule-Provider 开发流程入口（固定命令，等价于 CI 各步骤）
# 用法：make help

.PHONY: help test fetch generate validate check all clean

help:
	@echo "Rule-Provider 开发流程"
	@echo ""
	@echo "  make test        运行全部单元测试（零依赖）"
	@echo "  make fetch       下载数据源到 cache/（需联网）"
	@echo "  make generate    生成规则集到 ruleset/（读取 cache/）"
	@echo "  make validate    校验已生成规则集"
	@echo "  make check       生成 + 校验 + 幂等性检查（无需联网）"
	@echo "  make all         完整流程：fetch + check（每日更新等价）"
	@echo "  make clean       删除生成产物 ruleset/"

test:
	python3 run_tests.py

fetch:
	python3 scripts/generate.py fetch

generate:
	python3 scripts/generate.py generate

validate:
	python3 scripts/generate.py validate

check: generate validate
	@set -e; tmp=$$(mktemp -d); \
	find ruleset -type f ! -name metadata.json -exec md5sum {} + | md5sum > $$tmp/h1; \
	python3 scripts/generate.py generate; \
	find ruleset -type f ! -name metadata.json -exec md5sum {} + | md5sum > $$tmp/h2; \
	diff $$tmp/h1 $$tmp/h2 && echo "幂等性验证通过（重复生成输出逐字节一致）"; \
	rm -rf $$tmp

all: fetch check

clean:
	rm -rf ruleset
