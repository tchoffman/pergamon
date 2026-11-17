import os
from pathlib import Path
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent


def load_config(path: str | Path | None = None) -> dict:
    """Read rag/config.yaml, then override llm_model + embedding_model from the
    install config (config/install.local.yaml → models:). Resolve paths against
    the repo root.
    """
    cfg_path = Path(path) if path else REPO_ROOT / "rag" / "config.yaml"
    with open(cfg_path) as f:
        cfg = yaml.safe_load(f)

    for key in ("chroma_dir",):
        if key in cfg and not Path(cfg[key]).is_absolute():
            cfg[key] = str(REPO_ROOT / cfg[key])
    # The index lives with the rest of the library: a sandbox, or a configured
    # data drive (rag/storage.py). An unplugged drive is reported when used.
    try:
        from rag.storage import DEFAULT, data_dir
        d = data_dir(check=False)
        if d != DEFAULT:
            cfg["chroma_dir"] = str(d / "chroma")
    except Exception:
        pass

    # Model override from install config. Deferred import to avoid a cycle.
    try:
        from rag.install_config import load_effective_config, load_model_catalog
        eff = load_effective_config()
        catalog = load_model_catalog()
        chosen_primary = (eff.get("models") or {}).get("primary")
        chosen_embedding = (eff.get("models") or {}).get("embedding")
        if chosen_primary and chosen_primary in catalog["primary"]:
            cfg["llm_model"] = catalog["primary"][chosen_primary]["ollama_tag"]
        if chosen_embedding and chosen_embedding in catalog["embedding"]:
            cfg["embedding_model"] = catalog["embedding"][chosen_embedding]["ollama_tag"]
    except Exception:
        # Falling back to rag/config.yaml's defaults is fine if anything above fails.
        pass

    # Per-install settings that answers depend on (gitignored local file).
    try:
        from rag.install_config import INSTALL_LOCAL_PATH, load_yaml
        local = load_yaml(INSTALL_LOCAL_PATH) if INSTALL_LOCAL_PATH.exists() else {}
        for key in ("location", "llm_model"):
            if local.get(key):
                cfg[key] = local[key]
    except Exception:
        pass

    return cfg
