"""Serve the two repository-backed Agent Skills over MCP stdio."""

import hashlib
from pathlib import Path
import sys

import yaml
from mcp.server.extension import Extension, MethodBinding, ResourceBinding
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.resources import TextResource
from mcp_types import RequestParams, Result, PaginatedRequestParams
from mcp.shared.exceptions import MCPError
from mcp_types import INVALID_PARAMS


class GetParams(RequestParams):
    uri: str


class SkillList(Result):
    skills: list[dict]


class SkillEntry(Result):
    skill: dict


class PublishedSkills(Extension):
    identifier = "io.modelcontextprotocol/skills"

    def __init__(self, root):
        self.entries = {}
        self.files = []
        for path in sorted(root.glob("*/SKILL.md")):
            data = path.read_bytes()
            text = data.decode()
            meta = yaml.safe_load(text.split("---", 2)[1])
            uri = f"skill://retry-server/{path.parent.name}/SKILL.md"
            self.entries[uri] = dict(
                uri=uri,
                frontmatter=meta,
                resources=[
                    dict(
                        uri=uri,
                        size=len(data),
                        digest="sha256:" + hashlib.sha256(data).hexdigest(),
                    )
                ],
            )
            self.files.append(
                ResourceBinding(
                    TextResource(
                        uri=uri, name=meta["name"], mime_type="text/markdown", text=text
                    )
                )
            )

    def resources(self):
        return self.files

    def methods(self):
        versions = frozenset({"2026-07-28"})
        return [
            MethodBinding(
                "skills/list", PaginatedRequestParams, self.list_skills, versions
            ),
            MethodBinding("skills/get", GetParams, self.get_skill, versions),
        ]

    async def list_skills(self, ctx, params):
        if params.cursor:
            raise MCPError(code=INVALID_PARAMS, message="unknown cursor")
        return SkillList(skills=list(self.entries.values()))

    async def get_skill(self, ctx, params):
        if params.uri not in self.entries:
            raise MCPError(code=INVALID_PARAMS, message="unknown skill")
        return SkillEntry(skill=self.entries[params.uri])


if __name__ == "__main__":
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "skills"
    MCPServer("retry-server", extensions=[PublishedSkills(root)]).run()
