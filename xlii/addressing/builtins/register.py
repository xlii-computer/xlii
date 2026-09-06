"""Register all built-in addressing providers."""
from __future__ import annotations

from xlii.addressing import register
from xlii.addressing.builtins.file import FileProvider
from xlii.addressing.builtins.project import ProjectProvider
from xlii.addressing.builtins.conv import ConvProvider
from xlii.addressing.builtins.config import ConfigProvider
from xlii.addressing.builtins.docs import DocsProvider
from xlii.addressing.builtins.skills import SkillsProvider
from xlii.addressing.builtins.plugins import PluginsProvider
from xlii.addressing.builtins.persona import PersonaProvider
from xlii.addressing.builtins.mark import MarkProvider
from xlii.addressing.builtins.jobs import JobsProvider
from xlii.addressing.builtins.farm import FarmProvider
from xlii.addressing.builtins.market import MarketProvider
from xlii.addressing.builtins.plan import PlanProvider
from xlii.addressing.builtins.tasks import TasksProvider
from xlii.addressing.builtins.artifacts import ArtifactsProvider
from xlii.addressing.builtins.canvas import CanvasProvider
from xlii.addressing.builtins.locker import LockerProvider
from xlii.addressing.builtins.media import MediaProvider
from xlii.addressing.builtins.wiki import WikiProvider
from xlii.addressing.builtins.git import GitProvider
from xlii.addressing.builtins.gigwork import GigworkProvider
from xlii.addressing.builtins.map import MapProvider
from xlii.addressing.builtins.remote import RemoteFsProvider
from xlii.addressing.builtins.via import ViaProvider
from xlii.addressing.builtins.xlii_root import XliiProvider
from xlii.addressing.builtins.home import HomeProvider
from xlii.addressing.builtins.projects import ProjectsProvider
from xlii.addressing.builtins.results import ResultsProvider
from xlii.addressing.builtins.sources import SourcesProvider
from xlii.addressing.builtins.menu import MenuProvider
from xlii.addressing.builtins.history import HistoryProvider
from xlii.addressing.builtins.faceconfig import FaceConfigProvider
from xlii.addressing.builtins.taskmake import TaskMakeProvider
from xlii.addressing.builtins.pluginmake import PluginMakeProvider
from xlii.addressing.builtins.pluginform import PluginFormProvider
from xlii.addressing.builtins.bindmake import BindMakeProvider
from xlii.addressing.builtins.gigmake import GigMakeProvider
from xlii.addressing.builtins.remotemake import RemoteMakeProvider
from xlii.addressing.builtins.jidmake import JidMakeProvider
from xlii.addressing.builtins.install import InstallProvider


def register_builtins() -> None:
    register(FileProvider())
    register(ProjectProvider())
    register(ConvProvider())
    register(ConfigProvider())
    register(DocsProvider())
    register(SkillsProvider())
    register(PluginsProvider())
    register(PersonaProvider())
    register(MarkProvider())
    register(JobsProvider())
    register(FarmProvider())
    register(MarketProvider())
    register(PlanProvider())
    register(TasksProvider())
    register(LockerProvider())
    register(ArtifactsProvider())
    register(CanvasProvider())
    register(MediaProvider())
    register(WikiProvider())
    # xwiki:// — the VENDOR tier: xlii's own shipped self-docs (read-only, constant in
    # every project; the tool door's wiki). Same provider class, second root — the
    # "architected-for-two" scope seam, now populated. Registers even when the bundle
    # is absent (a bundleless source checkout lists empty rather than unknown-scheme).
    from xlii.selfwiki import selfwiki_root

    register(WikiProvider(root=selfwiki_root, scheme="xwiki", readonly=True))
    register(GitProvider())
    register(GigworkProvider())
    register(MapProvider())
    register(RemoteFsProvider("ftp"))
    register(RemoteFsProvider("sftp"))
    register(RemoteFsProvider("dav"))
    register(RemoteFsProvider("smb"))
    register(RemoteFsProvider("remote"))
    register(ViaProvider())
    register(XliiProvider())
    register(HomeProvider())
    register(ProjectsProvider())
    register(ResultsProvider())
    register(SourcesProvider())
    register(MenuProvider())
    register(HistoryProvider())
    register(FaceConfigProvider())
    register(TaskMakeProvider())
    register(PluginMakeProvider())
    register(PluginFormProvider())
    register(BindMakeProvider())
    register(GigMakeProvider())
    register(RemoteMakeProvider())
    register(JidMakeProvider())
    register(InstallProvider())
