<div align="center">

<img src="frontend/src/app/icon.png" alt="" width="96" height="96">

# Director's Cut — Foundry Video Studio

**基于 Microsoft Foundry 的多智能体视频工作室：把一份分镜脚本变成完整的多场景成片。**

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-backend-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Next.js](https://img.shields.io/badge/Next.js-dashboard-000000?logo=nextdotjs&logoColor=white)](https://nextjs.org/)
[![Azure Container Apps](https://img.shields.io/badge/Azure-Container%20Apps-0078D4?logo=microsoftazure&logoColor=white)](docs/deployment.md)

[快速上手](docs/getting-started.md) · [配置](docs/configuration.md) · [部署](docs/deployment.md) · [API](docs/api.md) · [English](README.md)

<img src="docs/images/job-overview.jpg" alt="已完成的四场景工作流：阶段进度与合成后的成片" width="900">

</div>

## 简介

粘贴一份脚本：Director 智能体将其拆分为场景，设计统一的角色形象，并为每个镜头绘制首帧；渲染智能体用你选择的视频模型生成每个片段；Composer 智能体把它们拼接成一个 MP4。你可以在仪表盘中逐场景审阅、改写提示词、单独重新生成或 remix 某个镜头，再重新合成成片，无需从头再来。

## 功能

- **从分镜到成片**：`**Scene N (Title)**` 段落依次变为场景提示词、视频片段和拼接成片；没有标题的脚本会被自动拆分。
- **四种视频引擎**：Azure OpenAI 上的 Sora 2、Kling、即梦（Jimeng）和 Seedance，可按任务选择。
- **角色一致**：从脚本中提取角色并生成参考图，每个场景都使用其中出场角色的参考图生成。
- **AI 首帧**：gpt-image-2.5 按所选风格（3D、2D 或写实）为每个场景绘制首帧，也可以上传自己的图片。
- **逐场景把控**：编辑提示词，单独重新生成或 remix 某个场景，保留所有版本，并选择哪个版本进入成片。
- **视频洞察与复刻**：用 Azure Content Understanding 分析现有视频，再逐段以新风格重新生成。
- **双语仪表盘**：中英文界面，支持浅色与深色主题。
- **一键上云**：一条脚本部署到 Azure Container Apps，任务数据持久化到私有 Blob Storage。

## 截图

<table>
  <tr>
    <td width="50%"><img src="docs/images/start-workflow.png" alt="启动工作流表单：脚本、引擎、分辨率、风格与首帧选项"></td>
    <td width="50%"><img src="docs/images/scene-editor.jpg" alt="场景编辑器：片段预览、可编辑提示词、角色参考图、首帧与版本"></td>
  </tr>
  <tr>
    <td align="center"><b>启动工作流</b>：脚本、引擎、风格与选项</td>
    <td align="center"><b>逐场景导演</b>：提示词、参考图与版本</td>
  </tr>
</table>

<p align="center"><img src="docs/images/workflow-runs.png" alt="工作流列表：状态、引擎、模式、分辨率与进度" width="900"></p>

## 架构

```mermaid
flowchart TB
    ui["Next.js 仪表盘<br/>分镜 · 审阅 · 版本"] <-->|REST| api["FastAPI 后端<br/>任务 · 进度 · 媒体"]

    subgraph pipeline["智能体流水线 · Microsoft Agent Framework"]
        direction LR
        director["Director 智能体<br/>场景规划 · 角色 · 首帧"] --> render["渲染智能体<br/>每个场景一个片段"] --> composer["Composer 智能体<br/>ffmpeg 拼接"] --> final(["最终 MP4"])
    end

    subgraph models["模型"]
        direction LR
        reasoning["推理模型<br/>脚本 · 提示词 · 视觉理解"]
        image["gpt-image-2.5<br/>参考图 · 首帧"]
        video["视频引擎<br/>Sora 2 · Kling · 即梦 · Seedance"]
        reasoning ~~~ image ~~~ video
    end

    subgraph azure["Azure 服务"]
        direction LR
        cu["Content Understanding<br/>视频洞察"]
        blob[("Blob Storage<br/>任务与媒体")]
        cu ~~~ blob
    end

    api --> pipeline
    pipeline -.-> models
    api -.-> azure
```

Director 调用推理模型和 gpt-image-2.5 生成场景规划、角色参考图与首帧；渲染智能体调用所选的视频引擎。任务状态保存在 TinyDB 中，云端会同步到 Blob Storage。视频洞察与视频复刻复用同一套能力来处理已有素材。

## 快速开始

```bash
cp .env.example .env              # 填写 Azure OpenAI / Foundry 配置
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.lock
(cd frontend && npm install)
./start-dev.sh                    # API 在 :8000，仪表盘在 :3000
```

打开 http://localhost:3000 ，点击 **Start Workflow**（启动工作流）并粘贴分镜脚本。完整安装步骤（包括各视频引擎的凭据）见[快速上手](docs/getting-started.md)。

## 文档

| 指南 | 内容 |
|---|---|
| [快速上手](docs/getting-started.md) | 本地安装、典型流程、命令行、项目结构、测试、常见问题 |
| [配置](docs/configuration.md) | 模型、视频引擎、Entra 登录、视频洞察、存储 |
| [部署](docs/deployment.md) | Azure Container Apps、更新、持久化存储 |
| [API 参考](docs/api.md) | 全部 REST 路由 |

详细文档目前为英文版。

---

<sub>由 Ming Gu（AI GBB 团队）基于 Microsoft Azure 构建。</sub>
