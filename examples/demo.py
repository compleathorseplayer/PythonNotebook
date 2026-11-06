# %% Setup
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
rng = np.random.default_rng(1)

# %% Data
x = np.linspace(0, 10, 200)
y = np.sin(x) + 0.3 * rng.standard_normal(200)
df = pd.DataFrame({"x": x, "y": y})
df.describe().round(3)

# %% Fit, printing as it goes
import time
for degree in (1, 3, 5, 7):
    coef = np.polyfit(x, y, degree)
    rmse = np.sqrt(np.mean((np.polyval(coef, x) - y) ** 2))
    print(f"degree {degree}: rmse {rmse:.3f}")
    time.sleep(0.5)

# %% Plot
plt.figure(figsize=(7, 3.2))
plt.scatter(x, y, s=8, alpha=0.5, label="data")
plt.plot(x, np.polyval(coef, x), color="C1", label=f"degree {degree} fit")
plt.legend();
