import argparse
import numpy as np
import json
from tmu.models.classification.vanilla_classifier import TMClassifier
from sklearn.datasets import fetch_openml

parser = argparse.ArgumentParser()
parser.add_argument("--dataset",     type=str,   default="mnist_784")
parser.add_argument("--clauses",     type=int,   default=100)
parser.add_argument("--T",           type=int,   default=500)
parser.add_argument("--s",           type=float, default=10.0)
parser.add_argument("--train",       type=int,   default=60000)
parser.add_argument("--samples",     type=int,   default=100,
                    help="Number of test samples to run inference on")
parser.add_argument("--output",      type=str,   default="tm_summary.json")
parser.add_argument("--per-request", action="store_true",
                    help="Also generate per-request fractions for gem5")
args = parser.parse_args()

# ── Load and train ────────────────────────────────────────────
print(f"Loading {args.dataset}...")
data = fetch_openml(args.dataset, version=1, as_frame=True)
X = (data.data.values > 127).astype(np.uint32)
y = data.target.values.astype(np.uint32)

num_literals = X.shape[1] * 2
num_classes  = len(set(y))

print(f"Training TM ({args.clauses} clauses)...")
tm = TMClassifier(
    number_of_clauses     = args.clauses,
    T                     = args.T,
    s                     = args.s,
    max_included_literals = num_literals
)
tm.fit(X[:args.train], y[:args.train])

acc = float(np.mean(
    tm.predict(X[args.train:args.train+args.samples]) ==
    y[args.train:args.train+args.samples]
) * 100)
print(f"  Accuracy : {acc:.1f}%")

# ── Extract mean active fractions (existing behaviour) ────────
print("Extracting mean TA patterns...")
all_fractions = []
for class_idx in range(num_classes):
    for clause_idx in range(args.clauses):
        included = sum(
            tm.get_ta_action(clause_idx, ta_idx, the_class=class_idx)
            for ta_idx in range(num_literals)
        )
        all_fractions.append(included / num_literals)

all_fractions = np.array(all_fractions)

summary = {
    "dataset":       args.dataset,
    "accuracy":      acc,
    "mean_fraction": float(np.mean(all_fractions)),
    "std_fraction":  float(np.std(all_fractions)),
    "min_fraction":  float(np.min(all_fractions)),
    "max_fraction":  float(np.max(all_fractions)),
    "num_clauses":   args.clauses,
    "num_literals":  num_literals,
    "num_classes":   num_classes,
    "num_samples":   args.samples,
}
with open(args.output, "w") as f:
    json.dump(summary, f, indent=2)
print(f"Saved {args.output} (mean fraction: {summary['mean_fraction']:.4f})")

# ── Per-request fractions (new behaviour) ─────────────────────
if args.per_request:
    print(f"\nGenerating per-request fractions for {args.samples} samples...")

    # For each test sample, for each class, for each clause:
    # compute fraction of literals that are active for THIS specific input
    #
    # active = TA=1 (included) AND literal=0 (false in this sample)
    # This is the exact discharge condition from the paper.
    #
    # Shape of output: (num_samples × num_classes × num_clauses,)
    # This matches the order gem5 will process requests.

    per_request = []

    X_test = X[args.train:args.train+args.samples]

    for sample_idx in range(args.samples):
        x = X_test[sample_idx]   # shape: (num_features,)

        # Expand to include negated literals
        # literal[i]   = x[i]
        # literal[i+n] = 1 - x[i]  (negated)
        n = len(x)
        literals = np.concatenate([x, 1 - x])   # shape: (num_literals,)

        for class_idx in range(num_classes):
            for clause_idx in range(args.clauses):

                # Get TA actions for this clause
                ta = np.array([
                    tm.get_ta_action(clause_idx, ta_idx, the_class=class_idx)
                    for ta_idx in range(num_literals)
                ])

                # Discharge condition: TA=1 AND literal=0
                discharge_cells = np.sum((ta == 1) & (literals == 0))
                fraction = discharge_cells / num_literals
                per_request.append(fraction)

        if (sample_idx + 1) % 10 == 0:
            print(f"  Processed {sample_idx+1}/{args.samples} samples...")

    per_request = np.array(per_request, dtype=np.float32)

    # Save alongside the summary
    npy_path = args.output.replace(".json", "_per_request.npy")
    np.save(npy_path, per_request)

    # Update summary with per-request stats
    summary["per_request_file"]        = npy_path
    summary["per_request_mean"]        = float(np.mean(per_request))
    summary["per_request_std"]         = float(np.std(per_request))
    summary["per_request_total"]       = len(per_request)
    with open(args.output, "w") as f:
        json.dump(summary, f, indent=2)

    print(f"\nSaved {npy_path}")
    print(f"  Total requests    : {len(per_request)}")
    print(f"  Mean fraction     : {np.mean(per_request):.4f}")
    print(f"  Std               : {np.std(per_request):.4f}")
    print(f"  vs mean-only      : {summary['mean_fraction']:.4f}  ← difference shows per-request matters")