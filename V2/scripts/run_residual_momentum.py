"""CLI entry point for preregistered residual momentum research."""
from pathlib import Path
import sys
root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root))
from strategies.residual_momentum import main
if __name__ == "__main__": main()
