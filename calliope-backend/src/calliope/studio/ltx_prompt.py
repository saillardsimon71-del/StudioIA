"""Compilation des prompts LTX-2.5.

Le format LTX est une prose visuelle fluide avec les répliques entre
guillemets. Les balises H3 (``<d>``), les timings absolus et les clés de
blocking brutes ne sont pas comprises par LTX et sont interdites par le
linter avant tout appel GPU. L'ancien compilateur est conservé dans
``work/audit/legacy/ltx_prompt.py`` pour comparaison.
"""

from .ltx_prompt_v3 import (  # noqa: F401
    LTX_PROMPT_MAX_WORDS,
    LTX_PROMPT_TARGET_WORDS,
    LTX_PROMPT_VERSION,
    compile_ltx_prompt,
    lint_ltx_prompt,
)
