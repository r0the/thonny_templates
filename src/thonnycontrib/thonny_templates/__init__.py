from .manifest import DEFAULT_MANIFEST_SOURCES


def load_plugin() -> None:
    from thonny import get_workbench

    from .dialogs import show_template_dialog

    workbench = get_workbench()
    workbench.set_default("templates.sources", "")
    workbench.set_default("templates.source", DEFAULT_MANIFEST_SOURCES[0].url)
    workbench.set_default("templates.target_dir", "")
    workbench.add_command(
        "open_project_templates",
        "tools",
        "Projektvorlagen…",
        show_template_dialog,
        group=50,
    )
