"""Phase 1 environment and packaging sanity checks."""

import pytest

import ai_quality_lab


@pytest.mark.setup
def test_package_imports_from_src():
    # src layout: the import only succeeds if the package was installed (pip install -e .),
    # and the editable install should resolve to the source tree, not a stray copy.
    assert ai_quality_lab.__version__ == "0.1.0"
    assert "src" in ai_quality_lab.__file__.replace("\\", "/").split("/")


@pytest.mark.setup
def test_placeholder_modules_import():
    from ai_quality_lab import evaluator, mock_agent, reporting, runner  # noqa: F401
