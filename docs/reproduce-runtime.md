# 原参考执行环境的恢复

当前快照的源码、runtime 基线与补丁均已核验。测试复用现有主机环境；本次没有创建新的 live sandbox 或重跑模型。历史 sandbox 的运行环境通过五个原始共享包及其 SHA-256 固定，见 `environment/reference-runtime-index.json`。

在兼容的 Linux x86_64 / Ubuntu 22 环境中，准备一个新的运行目录，恢复原 Python 和依赖 overlay：

```bash
TOOLS_ROOT=/mnt/shared-storage-user/songdemin/user/laibowen-p/webarena-verified/1.2.3/tools
EXECUTION_ROOT=/your/workspace/wav-execution
mkdir -p "$EXECUTION_ROOT/python" "$EXECUTION_ROOT/verified-deps" "$EXECUTION_ROOT/extra" "$EXECUTION_ROOT/nltk"
tar -xzf "$TOOLS_ROOT/python312-playwright144-chromium1117.tar.gz" --exclude='*/__pycache__*' --exclude='*.pyc' -C "$EXECUTION_ROOT/python"
tar -xzf "$TOOLS_ROOT/webarena-verified-venv.tar.gz" --strip-components=1 --exclude='*/__pycache__*' --exclude='*.pyc' -C "$EXECUTION_ROOT/verified-deps"
tar -xzf "$TOOLS_ROOT/webarena-api-extra.tar.gz" --strip-components=1 --exclude='*/__pycache__*' --exclude='*.pyc' -C "$EXECUTION_ROOT/extra"
tar -xzf "$TOOLS_ROOT/nltk-punkt-tab.tar.gz" --strip-components=1 -C "$EXECUTION_ROOT/nltk"
export PLAYWRIGHT_BROWSERS_PATH="$EXECUTION_ROOT/python/ms-playwright"
export NLTK_DATA="$EXECUTION_ROOT/nltk"
export PYTHONDONTWRITEBYTECODE=1
```

README 中的 `RUNTIME_ROOT` 应当已经恢复源码并应用补丁。先在未打 evaluator patch 的环境中运行独立 evaluator builder，再将构建目录放到 evaluator 依赖之前。保持原 overlay 优先级：

```bash
export PYTHONPATH="$RUNTIME_ROOT:$EXECUTION_ROOT/extra:$EXECUTION_ROOT/verified-deps/lib/python3.12/site-packages"
uv run --no-project --no-sync --python "$EXECUTION_ROOT/python/cpython-3.12.12-linux-x86_64-gnu/bin/python3.12" python devs/data/webarena_verified_generated/prepare_evaluator.py --output-root "$PWD/work/evaluator"
export PYTHONPATH="$RUNTIME_ROOT:$PWD/work/evaluator:$EXECUTION_ROOT/extra:$EXECUTION_ROOT/verified-deps/lib/python3.12/site-packages"
uv run --no-project --no-sync --python "$EXECUTION_ROOT/python/cpython-3.12.12-linux-x86_64-gnu/bin/python3.12" python devs/data/webarena_verified_generated/run_reference.py --help
```

`python-extra` 覆盖基础包中的部分版本，包括 Pydantic；清单按两个包分别记录。上述方法恢复原包内容和导入顺序，避免将历史 overlay 误写成一个已经 clean resolver 验证的锁文件。

浏览器还需要系统库。原运行镜像包 `webarena-eval-base-ubuntu22-playwright144.tar.gz` 及其校验值同样在 index 中记录。新容器、站点、模型凭据和 baseline receipt 由执行协调器在自己的资源范围内配置；单条 reference 入口不负责该生命周期。

官方 catalog 留在依赖包中，只允许生成完成后的 `validate.py` 使用；authoring 的 WebHarbor checkout 必须单独固定到规定 commit。原批次数据路径由 release 的 `data-index.json` 提供。
