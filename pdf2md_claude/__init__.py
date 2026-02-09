"""Claude native PDF → Markdown conversion package.

Uses Anthropic's Claude API with native PDF support to convert PDF documents
to high-quality Markdown. Provides significantly better table extraction,
symbol preservation, and OCR quality compared to traditional PDF parsing
pipelines.

Key features:
- Small-chunk conversion for maximum fidelity
- Context passing between disjoint chunks for seamless cross-page tables
- Continuation table merging across page boundaries
- Image extraction and injection from bounding-box markers (pymupdf)
- Content validation (page markers, tables, heading gaps, binary sequences,
  fabrication detection)
- Truncation detection with actionable error messages

Note: Imports are deferred to avoid requiring ``anthropic`` at import time.
Use explicit imports from submodules (e.g., ``from pdf2md_claude.models import ...``)
or access via this package after ``anthropic`` is installed.
"""

from importlib.metadata import version, PackageNotFoundError

try:
    __version__ = version("pdf2md-claude")
except PackageNotFoundError:
    __version__ = "0.0.0"  # fallback for uninstalled dev usage


def __getattr__(name: str):
    """Lazy imports to avoid requiring anthropic at package import time."""
    # Map attribute names to their source modules.
    _lazy_imports = {
        # claude.client
        "create_client": "pdf2md_claude.client",
        # pdf2md_claude.converter
        "ChunkResult": "pdf2md_claude.converter",
        "ConversionResult": "pdf2md_claude.converter",
        "convert_pdf": "pdf2md_claude.converter",
        "needs_conversion": "pdf2md_claude.converter",
        # pdf2md_claude.workdir
        "ChunkUsageStats": "pdf2md_claude.workdir",
        "WorkDir": "pdf2md_claude.workdir",
        # pdf2md_claude.markers
        "MarkerDef": "pdf2md_claude.markers",
        "PAGE_BEGIN": "pdf2md_claude.markers",
        "PAGE_END": "pdf2md_claude.markers",
        # pdf2md_claude.images
        "extract_and_inject_images": "pdf2md_claude.images",
        "ImageRect": "pdf2md_claude.images",
        "RenderedImage": "pdf2md_claude.images",
        # pdf2md_claude.merger
        "merge_chunks": "pdf2md_claude.merger",
        # pdf2md_claude.models
        "MODELS": "pdf2md_claude.models",
        "ModelConfig": "pdf2md_claude.models",
        "DocumentUsageStats": "pdf2md_claude.models",
        "calculate_cost": "pdf2md_claude.models",
        "fmt_duration": "pdf2md_claude.models",
        "format_summary": "pdf2md_claude.models",
        # pdf2md_claude.prompt
        "build_system_prompt": "pdf2md_claude.prompt",
        "SYSTEM_PROMPT": "pdf2md_claude.prompt",
        # pdf2md_claude.rules
        "AUTO_RULES_FILENAME": "pdf2md_claude.rules",
        "RulesFileResult": "pdf2md_claude.rules",
        "parse_rules_file": "pdf2md_claude.rules",
        "build_custom_system_prompt": "pdf2md_claude.rules",
        "generate_rules_template": "pdf2md_claude.rules",
        # pdf2md_claude.validator
        "validate_output": "pdf2md_claude.validator",
        "ValidationResult": "pdf2md_claude.validator",
    }

    if name in _lazy_imports:
        import importlib
        module = importlib.import_module(_lazy_imports[name])
        return getattr(module, name)

    raise AttributeError(f"module 'pdf2md_claude' has no attribute {name!r}")


__all__ = [
    "build_system_prompt",
    "ChunkResult",
    "ChunkUsageStats",
    "ConversionResult",
    "create_client",
    "convert_pdf",
    "extract_and_inject_images",
    "ImageRect",
    "MarkerDef",
    "PAGE_BEGIN",
    "PAGE_END",
    "merge_chunks",
    "needs_conversion",
    "RenderedImage",
    "validate_output",
    "ValidationResult",
    "MODELS",
    "ModelConfig",
    "DocumentUsageStats",
    "calculate_cost",
    "fmt_duration",
    "format_summary",
    "SYSTEM_PROMPT",
    "WorkDir",
    "AUTO_RULES_FILENAME",
    "RulesFileResult",
    "parse_rules_file",
    "build_custom_system_prompt",
    "generate_rules_template",
]
