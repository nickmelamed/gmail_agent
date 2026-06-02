from dataclasses import dataclass, field
from pathlib import Path
from typing import List


@dataclass
class UserProfile:
    name: str = ""
    role: str = ""
    professional_focus: List[str] = field(default_factory=list)
    tone: str = ""
    style_rules: List[str] = field(default_factory=list)
    availability: List[str] = field(default_factory=list)
    decision_rules: List[str] = field(default_factory=list)
    relationship_sensitivity: dict = field(default_factory=dict)
    signature: str = ""

    def to_prompt_xml(self) -> str:
        focus = "\n".join(f"  - {f}" for f in self.professional_focus)
        style = "\n".join(f"  - {s}" for s in self.style_rules)
        avail = "\n".join(f"  - {a}" for a in self.availability)
        rules = "\n".join(f"  - {r}" for r in self.decision_rules)
        rel = "\n".join(f"  - {k}: {v}" for k, v in self.relationship_sensitivity.items())

        return f"""<user_profile>
  <name>{self.name}</name>
  <role>{self.role}</role>
  <professional_focus>
{focus}
  </professional_focus>
  <tone>{self.tone}</tone>
  <style_rules>
{style}
  </style_rules>
  <availability>
{avail}
  </availability>
  <decision_rules>
{rules}
  </decision_rules>
  <relationship_sensitivity>
{rel}
  </relationship_sensitivity>
  <signature>{self.signature}</signature>
</user_profile>"""


def parse_profile(path: str | Path) -> UserProfile:
    text = Path(path).read_text(encoding="utf-8")
    lines = text.splitlines()
    profile = UserProfile()

    current_section = None
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        if stripped.startswith("Name:"):
            profile.name = stripped.split(":", 1)[1].strip()
        elif stripped.startswith("Role:"):
            profile.role = stripped.split(":", 1)[1].strip()
        elif stripped.startswith("Tone:"):
            profile.tone = stripped.split(":", 1)[1].strip()
        elif stripped.startswith("Signature:"):
            profile.signature = stripped.split(":", 1)[1].strip()
        elif stripped.endswith(":"):
            current_section = stripped[:-1].strip()
        elif stripped.startswith("- "):
            item = stripped[2:].strip()
            if current_section == "Professional Focus":
                profile.professional_focus.append(item)
            elif current_section == "Style":
                profile.style_rules.append(item)
            elif current_section == "Availability":
                profile.availability.append(item)
            elif current_section == "Decision Rules":
                profile.decision_rules.append(item)
            elif current_section == "Relationship Sensitivity":
                if ":" in item:
                    k, v = item.split(":", 1)
                    profile.relationship_sensitivity[k.strip()] = v.strip()

    return profile
