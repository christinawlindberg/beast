import numpy as np
from scipy.interpolate import interp1d
from scipy.integrate import quad

from beast.physicsmodel.grid_weights_stars import compute_bin_boundaries
import beast.physicsmodel.priormodel_functions as pmfuncs


__all__ = [
    "PriorModel",
    "PriorDustModel",
    "PriorAgeModel",
    "PriorMassModel",
    "PriorMetallicityModel",
    "PriorDistanceModel",
]


class PriorModel:
    """
    Compute the priors as weights given the input grid
    """

    def __init__(self, model, allowed_models=None):
        """
        Initialize with basic information

        Parameters
        ----------
        model: dict
          Choice of model type [default=flat]
          flat = flat prior
          lognormal = lognormal prior
          two_lognormal = two lognormal prior
          exponential = exponential prior
        """
        if (allowed_models is not None) and (model["name"] not in allowed_models):
            modname = model["name"]
            raise NotImplementedError(f"{modname} is not an allowed model")
        # save the model
        self.model = model

    def __call__(self, x):
        """
        Weights based on input model choice

        Parameters
        ----------
        x : float
            values for model evaluation
        """
        if self.model["name"] == "flat":
            if "amp" in self.model.keys():
                amp = self.model["amp"]
            else:
                amp = 1.0
            return np.full(x.shape, amp)
        elif self.model["name"] == "bins_histo":
            # check if all ages within interpolation range
            if np.all(
                [np.max(x) <= cval <= np.min(x) for cval in self.model["values"]]
            ):
                raise ValueError("bins_histo requested bins outside of model range")

            # interpolate according to bins, assuming value is constant from i to i+1
            # and allow for bin edges input
            if len(self.model["values"]) == len(self.model["x"]) - 1:
                self.model["values"].append(0.0)
            interfunc = interp1d(self.model["x"], self.model["values"], kind="zero")
            return interfunc(x)
        elif self.model["name"] == "bins_interp":
            # interpolate model to grid ages
            return np.interp(
                x,
                np.array(self.model["x"]),
                np.array(self.model["values"]),
            )
        elif self.model["name"] == "lognormal":
            return pmfuncs._lognorm(x, self.model["mean"], sigma=self.model["sigma"])
        elif self.model["name"] == "two_lognormal":
            return pmfuncs._two_lognorm(
                x,
                self.model["mean1"],
                self.model["mean2"],
                sigma1=self.model["sigma1"],
                sigma2=self.model["sigma2"],
                N1=self.model["N1_to_N2"],
                N2=1.0,
            )
        elif self.model["name"] == "exponential":
            return pmfuncs._exponential(x, tau=self.model["tau"])
        else:
            modname = self.model["name"]
            raise NotImplementedError(f"{modname} is not an allowed model")


class PriorDustModel(PriorModel):
    """
    Prior model for dust parameters with specific allowed models.
    """

    def __init__(self, model):
        super().__init__(
            model, allowed_models=["flat", "lognormal", "two_lognormal", "exponential"]
        )


class PriorAgeModel(PriorModel):
    """
    Prior model for age parameter with specific allowed models.
    """

    def __init__(self, model):
        super().__init__(
            model,
            allowed_models=[
                "flat",
                "flat_log",
                "bins_histo",
                "bins_interp",
                "exponential",
            ],
        )

    def __call__(self, x):
        """
        Weights based on input model choice

        Parameters
        ----------
        x : float
            values for model evaluation
        """
        if self.model["name"] == "flat_log":
            weights = 1.0 / np.diff(10 ** compute_bin_boundaries(x))
            return weights / np.sum(weights)
        elif self.model["name"] == "exponential":
            return pmfuncs._exponential(10.0 ** x, tau=self.model["tau"] * 1e9)
        else:
            return super().__call__(x)


class PriorMetallicityModel(PriorModel):
    """
    Prior model for metallicity parameter with specific allowed models.
    """

    def __init__(self, model):
        super().__init__(model, allowed_models=["flat"])


class PriorDistanceModel(PriorModel):
    """
    Prior model for distance parameter with specific allowed models.
    """

    def __init__(self, model):
        super().__init__(model, allowed_models=["flat"])


class PriorMassModel(PriorModel):
    """
    Prior model for mass parameter with specific allowed models.
    """

    def __init__(self, model):
        super().__init__(model, allowed_models=["flat", "salpeter", "kroupa"])

    def __call__(self, x):
        """
        Weights based on input model choice

        Parameters
        ----------
        x : float
            values for model evaluation
        """
        # sort the initial mass along this isochrone
        sindxs = np.argsort(x)

        # Compute the mass bin boundaries
        mass_bounds = compute_bin_boundaries(x[sindxs])

        # integrate the IMF over each bin
        if self.model["name"] == "kroupa":
            imf_func = pmfuncs._imf_kroupa
        elif self.model["name"] == "salpeter":
            imf_func = pmfuncs._imf_salpeter
        elif self.model["name"] == "flat":
            imf_func = pmfuncs._imf_flat

        # calculate the average prior in each mass bin
        mass_weights = np.zeros(len(x))
        for i, cindx in enumerate(sindxs):
            mass_weights[cindx] = (quad(imf_func, mass_bounds[i], mass_bounds[i + 1]))[0]
            mass_weights[cindx] /= (mass_bounds[i + 1] - mass_bounds[i])

        # normalize to avoid numerical issues (too small or too large)
        mass_weights /= np.average(mass_weights)

        return mass_weights
