"""Build and expose the multi-agent workflow."""

from agent_framework import WorkflowBuilder

from .agents import ComposerAgent, DirectorAgent, Sora2Agent


def create_sora_video_workflow():
    director = DirectorAgent()
    sora_agent = Sora2Agent()
    composer = ComposerAgent()

    builder = WorkflowBuilder()
    (
        builder.set_start_executor(director)
        .add_edge(director, sora_agent)
        .add_edge(sora_agent, composer)
    )
    return builder.build()


workflow = create_sora_video_workflow()


__all__ = ["create_sora_video_workflow", "workflow"]
