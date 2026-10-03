# WebArena-Verified 数据构造管线

本仓库封存当前 **v0.2.0-pilot** 管线，作为后续开发的起点。管线源码与本轮使用的版本逐文件一致；CUA-Lite runtime 通过固定基线 commit 和补丁复现。仓库已在本地初始化，初始提交、tag 和 GitHub 远端由仓库创建者设置。

本轮构造了 30 条单站任务，覆盖六个站点以及 RETRIEVE / NAVIGATE / MUTATE 三类任务。独立模型参考执行 25 条通过、5 条隔离；人工复核、canonical stage 和目标模型 export 尚待完成，当前训练准入数为 0。代码版本与数据准入状态分别记录。

## 仓库内容

| 路径 | 内容 |
| --- | --- |
| `devs/data/webarena_verified_generated/` | 原始管线入口、契约、6 个测试文件及 evaluator 补丁 |
| `integrations/cua-lite-v02.patch` | 本轮 BrowserGym 集成、动作修复和相关测试的完整差异 |
| `environment/` | 直接依赖版本与主机已安装包清单 |
| `releases/v0.2.0-pilot/` | 源码 manifest、数据索引、原始测试记录和本轮结果 |
| `examples/` | 不含凭据的模型和站点配置示例 |
| `docs/pipeline.md` | 当前流程、阶段 owner 和待开发事项 |

保留原脚本路径和调用方式，代码无需迁移即可继续修改。完整数据、HAR、trace、Parquet 和历史尝试保存在共享盘；路径和 SHA-256 由 `releases/v0.2.0-pilot/data-index.json` 提供。

## 运行环境

使用 Linux / Python 3.12。当前依赖固定为 WebArena-Verified 1.2.3、BrowserGym 0.14.3、Playwright 1.44.0，runtime 基线为 CUA-Lite `7125dc89c9b47e08afaedaa281c2f067e374c421`。

先恢复 runtime。将下面的 `RUNTIME_ROOT` 指向一个新的工作目录：

```bash
RUNTIME_ROOT=/your/workspace/cua-lite-runtime
mkdir -p "$RUNTIME_ROOT"
tar -xzf /mnt/shared-storage-user/songdemin/user/laibowen-p/webarena-verified/1.2.3/code/v0.2.0-pilot/cua-lite-base-7125dc89.tar.gz -C "$RUNTIME_ROOT"
git -C "$RUNTIME_ROOT" apply --check "$PWD/integrations/cua-lite-v02.patch"
git -C "$RUNTIME_ROOT" apply "$PWD/integrations/cua-lite-v02.patch"
export PYTHONPATH="$RUNTIME_ROOT${PYTHONPATH:+:$PYTHONPATH}"
```

该 runtime 压缩包保留原 commit 中的 `lite/`、`tests/` 和安装所需元数据，补丁补齐本轮尚未提交的改动。也可以自行 clone CUA-Lite、checkout 同一 commit 后应用补丁。基线压缩包及补丁的 SHA-256 见 `source-manifest.json`。

软件源可用时，测试和离线编译所需依赖可按下列命令安装。本轮封存的测试使用现有主机依赖及恢复后的源码，未新建安装环境：

```bash
uv sync --group dev
PYTHONDONTWRITEBYTECODE=1 uv run --no-sync pytest devs/data/webarena_verified_generated/tests -p no:cacheprovider -n 0
```

执行真实 GUI 参考轨迹时，还需要安装 runtime API 依赖、BrowserGym 的 WebArena base integration、Chromium 和 `punkt_tab`；站点由执行协调器提供。原 runtime installer 保存在补丁中：

```bash
uv pip install -r environment/requirements-reference.txt
uv run --no-sync bash "$RUNTIME_ROOT/lite/gym/envs/browsergym/scripts/install.sh" webarena_verified
```

`environment/requirements-reference.txt` 记录本轮 API 扩展包中的直接依赖版本。主机与沙盒存在依赖 overlay 差异，完整历史执行仍以原运行环境和批次收据为准；本次源码封存没有重新执行线上模型或站点任务，也没有生成新的完整传递依赖 lock。两次 lock 尝试受到内部软件源超时/502 阻断，详情在 release 的 `snapshot-verification.json` 中记录。

无需重新解析依赖的原运行环境恢复方式见 [docs/reproduce-runtime.md](/docs/reproduce-runtime.md)；五个原始环境包的 SHA-256 与包版本清单见 `environment/reference-runtime-index.json`。

## 入口与 evaluator 构建

所有入口保留原参数，可先运行 `--help`：

```bash
uv run --no-sync python devs/data/webarena_verified_generated/author.py --help
uv run --no-sync python devs/data/webarena_verified_generated/replay.py --help
uv run --no-sync python devs/data/webarena_verified_generated/run_reference.py --help
uv run --no-sync python devs/data/webarena_verified_generated/quality_check.py --help
```

最终 evaluator 使用独立构建的 AR null 修复，不修改已安装依赖：

```bash
uv run --no-sync python devs/data/webarena_verified_generated/prepare_evaluator.py --output-root "$PWD/work/evaluator"
export PYTHONPATH="$PWD/work/evaluator:$RUNTIME_ROOT${PYTHONPATH:+:$PYTHONPATH}"
PYTHONDONTWRITEBYTECODE=1 uv run --no-sync pytest devs/data/webarena_verified_generated/tests -p no:cacheprovider -n 0
```

构建目录必须不存在。`author.py` 要求独立 WebHarbor checkout，且 HEAD 为 `381dbd9882680806c81ed626d226f114c464a321`。官方 812 任务 catalog 仅供构造完成后的污染检查使用；生成流程不得读取其中的具体题面、参数或 GT。

## 初始化 GitHub 版本

创建空 GitHub 仓库后，替换下面的远端地址并执行。当前工作目录未暂存、未提交。

```bash
git init -b main
git add .
git commit -m "Freeze v0.2 pilot pipeline and reproducibility metadata"
git tag -a v0.2.0-pilot -m "30-task pilot; 25 reference passes; human and data promotion gates pending"
git remote add origin git@github.com:YOUR_ACCOUNT/webarena-verified-pipeline.git
git push -u origin main
git push origin v0.2.0-pilot
```

本地目录已经初始化过；`git init` 也用于分发压缩包解压后的首次初始化。

后续开发从该提交创建功能分支。原始 release 收据保留为历史快照，新版本的源码和数据索引写入新的 release 目录。本轮五条隔离任务的结果不能因代码版本升级而改写。
