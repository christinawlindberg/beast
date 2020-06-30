import os
import shutil
import tempfile
import numpy as np
import copy
import pytest
import pkg_resources

import tables

from astropy.tests.helper import remote_data
from astropy import constants as const
from astropy.table import Table
from astropy.io import fits

from beast.physicsmodel.stars.isochrone import ezIsoch
from beast.physicsmodel.dust import extinction
from beast.physicsmodel.grid import SpectralGrid, SEDGrid
from beast.physicsmodel.model_grid import (
    make_iso_table,
    make_spectral_grid,
    add_stellar_priors,
    make_extinguished_sed_grid,
)

from beast.observationmodel.noisemodel import generic_noisemodel as noisemodel
from beast.observationmodel.noisemodel.absflux_covmat import hst_frac_matrix
from beast.observationmodel.ast import make_ast_input_list
from beast.observationmodel.observations import Observations, gen_SimObs_from_sedgrid

from beast.fitting.trim_grid import trim_models
from beast.fitting import fit

from beast.tools import (
    get_libfiles,
    beast_settings,
    subgridding_tools,
    star_type_probability,
)
from beast.tools.read_beast_data import (
    read_lnp_data,
    read_noise_data,
    read_sed_data,
    get_lnp_grid_vals,
)
from beast.tools.compare_spec_type import compare_spec_type
from beast.tools.run import create_physicsmodel, create_obsmodel

from beast.tests.helpers import (
    download_rename,
    compare_hdf5,
    compare_tables,
    compare_fits,
)


@remote_data
class TestRegressionSuite:
    """
    The regression tests are done in a class to so that files are only
    downloaded once and can be used by multiple tests.
    """

    # download the BEAST library files
    get_libfiles.get_libfiles()

    # download the cached version for use and comparision
    # - photometry and ASTs
    obs_fname_cache = download_rename("b15_4band_det_27_A.fits")
    asts_fname_cache = download_rename("fake_stars_b15_27_all.hd5")
    # - isochrones
    iso_fname_cache = download_rename("beast_example_phat_iso.csv")
    # - spectra
    spec_fname_cache = download_rename("beast_example_phat_spec_grid.hd5")
    # - spectra with priors
    priors_fname_cache = download_rename("beast_example_phat_spec_w_priors.grid.hd5")
    priors_sub0_fname_cache = download_rename(
        "beast_example_phat_subgrids_spec_w_priors.gridsub0.hd5"
    )
    priors_sub1_fname_cache = download_rename(
        "beast_example_phat_subgrids_spec_w_priors.gridsub1.hd5"
    )
    # - SED grids
    seds_fname_cache = download_rename("beast_example_phat_seds.grid.hd5")
    seds_sub0_fname_cache = download_rename(
        "beast_example_phat_subgrids_seds.gridsub0.hd5"
    )
    seds_sub1_fname_cache = download_rename(
        "beast_example_phat_subgrids_seds.gridsub1.hd5"
    )
    # - noise model
    noise_fname_cache = download_rename("beast_example_phat_noisemodel.grid.hd5")
    noise_sub0_fname_cache = download_rename(
        "beast_example_phat_subgrids_noisemodel.gridsub0.hd5"
    )
    noise_sub1_fname_cache = download_rename(
        "beast_example_phat_subgrids_noisemodel.gridsub1.hd5"
    )
    # - trimmed files
    noise_trim_fname_cache = download_rename(
        "beast_example_phat_noisemodel_trim.grid.hd5"
    )
    seds_trim_fname_cache = download_rename("beast_example_phat_seds_trim.grid.hd5")
    # - output files
    stats_fname_cache = download_rename("beast_example_phat_stats.fits")
    lnp_fname_cache = download_rename("beast_example_phat_lnp.hd5")
    pdf1d_fname_cache = download_rename("beast_example_phat_pdf1d.fits")
    pdf2d_fname_cache = download_rename("beast_example_phat_pdf2d.fits")

    # create the beast_settings object
    # (copied over from the phat_small example in beast-examples)
    settings_path = pkg_resources.resource_filename("beast", "tests/")
    settings = beast_settings.beast_settings(
        settings_path + "beast_settings_for_tests.txt"
    )
    # update names of photometry and AST files
    settings.obsfile = obs_fname_cache
    settings.astfile = asts_fname_cache
    # also make a version with 2 subgrids
    settings_sg = copy.deepcopy(settings)
    settings_sg.n_subgrid = 2
    settings_sg.project = "beast_example_phat_subgrids"

    # ###################################################################
    # Standard BEAST fitting steps

    def test_padova_isochrone_download(self):
        """
        Generate the padova isochrone table and compare the result to a cached version.
        """
        # download the file live from the website
        savename = tempfile.NamedTemporaryFile(suffix=".csv").name
        (iso_fname, g) = make_iso_table(
            "test",
            iso_fname=savename,
            logtmin=self.settings.logt[0],
            logtmax=self.settings.logt[1],
            dlogt=self.settings.logt[2],
            z=self.settings.z,
        )

        # read the cached and new tables using astropy tables
        table_cache = Table.read(
            self.iso_fname_cache, format="ascii.csv", comment="#", delimiter=","
        )
        table_new = Table.read(
            iso_fname, format="ascii.csv", comment="#", delimiter=","
        )

        # compare
        compare_tables(table_cache, table_new)

    def test_make_kurucz_tlusty_spectral_grid(self):
        """
        Generate the spectral grid based on Kurucz and Tlusty stellar atmosphere
        models based on a cached set of isochrones and compare the result to a cached
        version.
        """
        # read in the cached isochrones
        oiso = ezIsoch(self.iso_fname_cache)

        # calculate the redshift
        redshift = (self.settings.velocity / const.c).decompose().value

        # make the spectral grid
        spec_fname = tempfile.NamedTemporaryFile(suffix=".hd5").name
        (spec_fname, g) = make_spectral_grid(
            "test",
            oiso,
            osl=self.settings.osl,
            redshift=redshift,
            distance=self.settings.distances,
            distance_unit=self.settings.distance_unit,
            spec_fname=spec_fname,
            # filterLib=filter_fname,
            extLaw=self.settings.extLaw,
            add_spectral_properties_kwargs=self.settings.add_spectral_properties_kwargs,
        )

        # compare the new to the cached version
        compare_hdf5(self.spec_fname_cache, spec_fname)

    def test_add_stellar_priors_to_spectral_grid(self):
        """
        Add the stellar priors to the a cached spectral grid and compare
        it to the cached version.
        """
        specgrid = SpectralGrid(self.spec_fname_cache, backend="memory")

        priors_fname = tempfile.NamedTemporaryFile(suffix=".hd5").name
        priors_fname, g = add_stellar_priors(
            "test", specgrid, priors_fname=priors_fname
        )

        # compare the new to the cached version
        compare_hdf5(self.priors_fname_cache, priors_fname)

    def test_make_extinguished_sed_grid(self):
        """
        Generate the extinguished SED grid using a cached version of the
        spectral grid with priors and compare the result to a cached version.
        """

        g_pspec = SpectralGrid(self.priors_fname_cache, backend="memory")

        # generate the SED grid by integrating the filter response functions
        #   effect of dust extinction applied before filter integration
        #   also computes the dust priors as weights
        seds_fname = tempfile.NamedTemporaryFile(suffix=".hd5").name
        (seds_fname, g) = make_extinguished_sed_grid(
            "test",
            g_pspec,
            self.settings.filters,
            seds_fname=seds_fname,
            extLaw=extinction.Gordon16_RvFALaw(),
            av=[0.0, 10.055, 1.0],
            rv=[2.0, 6.0, 1.0],
            fA=[0.0, 1.0, 0.25],
            av_prior_model={"name": "flat"},
            rv_prior_model={"name": "flat"},
            fA_prior_model={"name": "flat"},
            add_spectral_properties_kwargs=self.settings.add_spectral_properties_kwargs,
        )

        # compare the new to the cached version
        compare_hdf5(self.seds_fname_cache, seds_fname)

    def test_toothpick_noisemodel(self):
        """
        Generate the nosiemodel (aka observationmodel) using a cached version of
        the artifical star test results (ASTs) and compare the result to a cached
        version.
        """

        # get the modelsedgrid on which to generate the noisemodel
        modelsedgrid = SEDGrid(self.seds_fname_cache)

        # generate the AST noise model
        noise_fname = tempfile.NamedTemporaryFile(suffix=".hd5").name
        noisemodel.make_toothpick_noise_model(
            noise_fname,
            self.asts_fname_cache,
            modelsedgrid,
            absflux_a_matrix=self.settings.absflux_a_matrix,
            use_rate=False,
        )

        # compare the new to the cached version
        compare_hdf5(self.noise_fname_cache, noise_fname)

    def test_trim_grid(self):
        """
        Generate trim the sed grid and noise model using cached versions of the
        both and compare the result to a cached version.
        """
        # read in the observed data
        obsdata = Observations(
            self.obs_fname_cache, self.settings.filters, self.settings.obs_colnames
        )

        # get the modesedgrid
        modelsedgrid = SEDGrid(self.seds_fname_cache)

        # read in the noise model just created
        noisemodel_vals = noisemodel.get_noisemodelcat(self.noise_fname_cache)

        # trim the model sedgrid
        seds_trim_fname = tempfile.NamedTemporaryFile(suffix=".hd5").name
        noise_trim_fname = tempfile.NamedTemporaryFile(suffix=".hd5").name

        trim_models(
            modelsedgrid,
            noisemodel_vals,
            obsdata,
            seds_trim_fname,
            noise_trim_fname,
            sigma_fac=3.0,
        )

        # compare the new to the cached version
        compare_hdf5(self.seds_trim_fname_cache, seds_trim_fname, ctype="seds")
        compare_hdf5(self.noise_trim_fname_cache, noise_trim_fname, ctype="noise")

    def test_fit_grid(self):
        """
        Fit a cached version of the observations with cached version of the
        trimmed sed grid and noisemodel and compare the result to cached
        versions of the stats and pdf1d files.
        """
        # read in the the AST noise model
        noisemodel_vals = noisemodel.get_noisemodelcat(self.noise_trim_fname_cache)

        # read in the observed data
        obsdata = Observations(
            self.obs_fname_cache, self.settings.filters, self.settings.obs_colnames
        )
        # output files
        stats_fname = tempfile.NamedTemporaryFile(suffix=".fits").name
        pdf1d_fname = tempfile.NamedTemporaryFile(suffix=".fits").name
        lnp_fname = tempfile.NamedTemporaryFile(suffix=".hd5").name

        fit.summary_table_memory(
            obsdata,
            noisemodel_vals,
            self.seds_trim_fname_cache,
            threshold=-10.0,
            save_every_npts=100,
            lnp_npts=60,
            max_nbins=100,
            stats_outname=stats_fname,
            pdf1d_outname=pdf1d_fname,
            lnp_outname=lnp_fname,
        )

        # check that the stats files are exactly the same
        table_cache = Table.read(self.stats_fname_cache)
        table_new = Table.read(stats_fname)

        compare_tables(table_cache, table_new)

        # lnp files not checked as they are randomly sparsely sampled
        #   hence will be different every time the fitting is run

        # check that the pdf1d files are exactly the same
        compare_fits(self.pdf1d_fname_cache, pdf1d_fname)

    # ###################################################################
    # AST tests
    def test_ast_pick_models(self):
        """
        Generate the artifial star test (AST) inputs using a cached version of
        the sed grid and compare the result to a cached version.
        """
        # download files specific to this test
        cached_table_filename = download_rename("cache_inputAST.txt")

        mag_cuts = [1.0]

        outname = tempfile.NamedTemporaryFile(suffix=".txt").name
        make_ast_input_list.pick_models(
            self.seds_fname_cache,
            self.settings.filters,
            mag_cuts,
            outfile=outname,
            ranseed=1234,
        )

        table_new = Table.read(outname, format="ascii")

        # download cached version of the file and compare it to new file
        table_cache = Table.read(cached_table_filename, format="csv", delimiter=" ")
        compare_tables(table_new, table_cache)

    # ###################################################################
    # simulation tests
    def test_simobs(self):
        """
        Simulate observations using cached versions of the sed grid and noise model
        and compare the result to a cached version.
        """
        # download files specific to this test
        simobs_fname_cache = download_rename("beast_example_phat_simobs.fits")

        # get the physics model grid - includes priors
        modelsedgrid = SEDGrid(self.seds_fname_cache)

        # read in the noise model - includes bias, unc, and completeness
        noisegrid = noisemodel.get_noisemodelcat(self.noise_fname_cache)

        table_new = gen_SimObs_from_sedgrid(
            modelsedgrid, noisegrid, nsim=100, ranseed=1234,
        )

        # check that the simobs files are exactly the same
        table_cache = Table.read(simobs_fname_cache)

        # to avoid issues with uppercase vs lowercase column names, make them all
        # the same before comparing
        for col in table_new.colnames:
            table_new[col].name = col.upper()
        for col in table_cache.colnames:
            table_cache[col].name = col.upper()

        compare_tables(table_cache, table_new)

    # ###################################################################
    # tools tests
    def test_read_lnp_data(self):
        """
        Read in the lnp data from a cached file and test that selected values
        are as expected.
        """
        ldata = read_lnp_data(self.lnp_fname_cache)

        exp_keys = ["vals", "indxs"]
        for ckey in ldata.keys():
            assert ckey in exp_keys, f"{ckey} not in lnp data expected keys"

        # check an entry for a single model (caching current values 20 Apr 2020)
        # fmt: off
        exp_vals = [-56.83604431, -76.34762573, -17.55770874, -18.23323059, -10.53744507]
        exp_indxs = [14639., 15015., 296., 12636., 1336.]
        # fmt: on
        np.testing.assert_allclose(
            ldata["vals"][0][0:5],
            exp_vals,
            err_msg="Expected posterior (vals) values not correct",
        )
        np.testing.assert_allclose(
            ldata["indxs"][0][0:5],
            exp_indxs,
            err_msg="Expected index values not correct",
        )

    def test_read_noise_data(self):
        """
        Read in the noise model from a cached file and test that selected values
        are as expected.
        """
        ndata = read_noise_data(self.noise_trim_fname_cache)

        exp_keys = ["bias", "completeness", "error"]
        for ckey in ndata.keys():
            assert ckey in exp_keys, f"{ckey} not in noise data expected keys"

        # check an entry for a single model (caching current values 18 Apr 2020)
        # fmt: off
        exp_bias = [-6.77602149e-20, -1.36353610e-20, 2.87448605e-20,
                    -2.38253474e-21, -1.70330281e-20, -2.70390708e-20]
        exp_error = [1.63128160e-19, 7.50503350e-20, 7.65873857e-20,
                     2.48842055e-20, 9.41313147e-20, 2.79650823e-20]
        exp_compl = [1.0, 0.95552407, 1.0, 0.74733078, 0.77777778, 0.42857143]
        # fmt: on
        np.testing.assert_allclose(
            ndata["bias"][10], exp_bias, err_msg="Expected bias values not correct",
        )
        np.testing.assert_allclose(
            ndata["error"][10], exp_error, err_msg="Expected error values not correct",
        )
        np.testing.assert_allclose(
            ndata["completeness"][10],
            exp_compl,
            err_msg="Expected completeness values not correct",
        )

    def test_read_sed_data(self):
        """
        Read in the sed grid from a cached file and test that selected values
        are as expected.
        """
        requested_params = ["Av", "Rv", "f_A", "M_ini", "logA", "Z", "distance"]

        # check that when return_params=True, then just a list of parameters is returned
        sparams = read_sed_data(self.seds_trim_fname_cache, return_params=True)
        assert isinstance(sparams, list), "Returned params are not a list"
        checknames = requested_params + ["seds", "lamb"]
        for cname in checknames:
            assert cname in sparams, f"{cname} not in sed parameter list"

        # check that otherwise, the requested sed data is returned
        sdata = read_sed_data(self.seds_trim_fname_cache, param_list=requested_params)
        expected_values = {
            "Av": 0.0,
            "Rv": 2.0,
            "f_A": 1.0,
            "M_ini": 4.0073261261,
            "logA": 6.0,
            "Z": 0.008,
            "distance": 783429.642766212,
        }
        for cname in requested_params:
            assert cname in sdata.keys(), f"requsted parameter {cname} not in sed data"
            np.testing.assert_allclose(
                sdata[cname][10],
                expected_values[cname],
                err_msg=f"expected value of {cname} is not found",
            )

    def test_get_lnp_grid_vals(self):
        """
        Read in the lnp and sed grid data from cached files and test that
        selected values are as expected.
        """
        ldata = read_lnp_data(self.lnp_fname_cache)

        requested_params = ["Av", "Rv", "f_A", "M_ini", "logA", "Z", "distance"]
        sdata = read_sed_data(self.seds_trim_fname_cache, param_list=requested_params)

        lgvals_data = get_lnp_grid_vals(sdata, ldata)

        # check that otherwise, the requested lgvals data is returned
        expected_values = {
            "Av": [0.0, 0.0, 0.0, 0.0, 0.0],
            "Rv": [2.0, 2.0, 2.0, 2.0, 2.0],
            "f_A": [1.0, 1.0, 1.0, 1.0, 1.0],
            "M_ini": [3.89416909, 3.92726111, 3.95603228, 2.04966068, 2.04999995],
            "logA": [6.0, 6.0, 6.0, 9.0, 9.0],
            "Z": [0.03, 0.03, 0.03, 0.004, 0.004],
            "distance": [
                783429.64276621,
                783429.64276621,
                783429.64276621,
                783429.64276621,
                783429.64276621,
            ],
        }
        for cname in requested_params:
            assert (
                cname in lgvals_data.keys()
            ), f"requsted parameter {cname} not in sed data"
            np.testing.assert_allclose(
                lgvals_data[cname][0:5, 10],
                expected_values[cname],
                err_msg=f"expected value of {cname} is not found",
            )

    def test_split_grid(self):
        """
        Split a cached version of a sed grid with various into a few different
        subgrids and check the splits are as expected.
        """
        split_and_check(self.seds_trim_fname_cache, 4)  # an edge case
        split_and_check(self.seds_trim_fname_cache, 3)  # an odd numer
        split_and_check(self.seds_trim_fname_cache, 1)  # an even number

    def test_reduce_grid_info(self):
        """
        Split a cached version of a sed grid and check that [not quite
        sure what this is checking - details needed].
        """
        sub_fnames = subgridding_tools.split_grid(self.seds_trim_fname_cache, 3)

        complete_g_info = subgridding_tools.subgrid_info(self.seds_trim_fname_cache)
        cap_unique = 50
        sub_g_info = subgridding_tools.reduce_grid_info(
            sub_fnames, nprocs=3, cap_unique=cap_unique
        )

        for q in complete_g_info:
            if q not in sub_g_info:
                raise AssertionError()
            if not complete_g_info[q]["min"] == sub_g_info[q]["min"]:
                raise AssertionError()
            if not complete_g_info[q]["max"] == sub_g_info[q]["max"]:
                raise AssertionError()
            num_unique = len(complete_g_info[q]["unique"])
            if num_unique > cap_unique:
                # Cpan still be larger if one of the sub results during the
                # reduction is larger. This is as intended.
                if not sub_g_info[q]["num_unique"] >= cap_unique:
                    raise AssertionError()
            else:
                if not sub_g_info[q]["num_unique"] == num_unique:
                    raise AssertionError()

    def test_merge_pdf1d_stats(self):
        """
        Using cached versions of the observations, sed grid, and noise model,
        split the grids and do the fitting on the subgrids and original
        grid.  Merge the results from the subgrids and compare to the results
        from fitting the full grid.
        """
        ######################################
        # STEP 1: GET SOME DATA TO WORK WITH #
        ######################################

        # read in the observed data
        obsdata = Observations(
            self.obs_fname_cache, self.settings.filters, self.settings.obs_colnames
        )

        #########################################################################################
        # STEP 2: SPLIT THE GRIDS AND GENERATE THE GRID INFO DICT AS IN THE SUBGRIDDING EXAMPLE #
        #########################################################################################
        num_subgrids = 3

        # Split SED grid
        sub_seds_trim_fnames = subgridding_tools.split_grid(
            self.seds_trim_fname_cache, num_subgrids, overwrite=True
        )

        # Split noise grid (a standardized function does not exist)
        sub_noise_trim_fnames = []

        noisemodel_vals = noisemodel.get_noisemodelcat(self.noise_trim_fname_cache)
        slices = subgridding_tools.uniform_slices(
            len(noisemodel_vals["bias"]), num_subgrids
        )
        for i, slc in enumerate(slices):
            outname = self.noise_trim_fname_cache.replace(".hd5", "sub{}.hd5".format(i))
            with tables.open_file(outname, "w") as outfile:
                outfile.create_array(outfile.root, "bias", noisemodel_vals["bias"][slc])
                outfile.create_array(
                    outfile.root, "error", noisemodel_vals["error"][slc]
                )
                outfile.create_array(
                    outfile.root, "completeness", noisemodel_vals["completeness"][slc]
                )
            sub_noise_trim_fnames.append(outname)

        # Collect information about the parameter rangers, to make the pdf1d bins
        # consistent between subgrids
        grid_info_dict = subgridding_tools.reduce_grid_info(
            sub_seds_trim_fnames, sub_noise_trim_fnames, nprocs=1, cap_unique=100
        )

        ##################################################
        # STEP 3: GENERATE FILENAMES AND RUN THE FITTING #
        ##################################################
        def make_gridsub_fnames(base_fname, num_subgrids, extension=".fits"):
            return [
                base_fname.replace(extension, "gridsub{}{}".format(i, extension))
                for i in range(num_subgrids)
            ]

        stats_fname = tempfile.NamedTemporaryFile(suffix=".fits").name
        pdf1d_fname = tempfile.NamedTemporaryFile(suffix=".fits").name
        lnp_fname = tempfile.NamedTemporaryFile(suffix=".hd5").name

        subgrid_pdf1d_fnames = make_gridsub_fnames(pdf1d_fname, num_subgrids)
        subgrid_stats_fnames = make_gridsub_fnames(stats_fname, num_subgrids)
        subgrid_lnp_fnames = make_gridsub_fnames(
            lnp_fname, num_subgrids, extension=".hd5"
        )

        for i in range(num_subgrids):
            sub_noisemodel_vals = noisemodel.get_noisemodelcat(sub_noise_trim_fnames[i])
            fit.summary_table_memory(
                obsdata,
                sub_noisemodel_vals,
                sub_seds_trim_fnames[i],
                threshold=-40.0,
                save_every_npts=100,
                lnp_npts=60,
                stats_outname=subgrid_stats_fnames[i],
                pdf1d_outname=subgrid_pdf1d_fnames[i],
                lnp_outname=subgrid_lnp_fnames[i],
                grid_info_dict=grid_info_dict,
                do_not_normalize=True,
            )
            # The do_not_normalize option is absolutely crucial!

        # Now merge the results
        merged_pdf1d_fname, merged_stats_fname = subgridding_tools.merge_pdf1d_stats(
            subgrid_pdf1d_fnames, subgrid_stats_fnames
        )

        # Do a full fit also
        normal_stats = tempfile.NamedTemporaryFile(suffix=".fits").name
        normal_pdf1d = tempfile.NamedTemporaryFile(suffix=".fits").name
        normal_lnp = tempfile.NamedTemporaryFile(suffix=".hd5").name
        fit.summary_table_memory(
            obsdata,
            noisemodel_vals,
            self.seds_trim_fname_cache,
            threshold=-40.0,
            save_every_npts=100,
            lnp_npts=60,
            stats_outname=normal_stats,
            pdf1d_outname=normal_pdf1d,
            lnp_outname=normal_lnp,
            do_not_normalize=True,
        )
        # Here, we also need to use do_not_normalize, otherwise Pmax will be
        # different by a factor

        # CHECKS
        tolerance = 1e-6
        fits_normal = fits.open(normal_pdf1d)
        fits_new = fits.open(merged_pdf1d_fname)

        if not len(fits_new) == len(fits_normal):
            raise AssertionError()

        # A similar problem to the above will also occur here
        for k in range(1, len(fits_new)):
            qname = fits_new[k].header["EXTNAME"]
            np.testing.assert_allclose(
                fits_new[k].data,
                fits_normal[qname].data,
                rtol=tolerance,
                atol=tolerance,
            )

        table_normal = Table.read(normal_stats)
        table_new = Table.read(merged_stats_fname)

        if not len(table_normal) == len(table_new):
            raise AssertionError()

        # These will normally fail, as the merging process can not be made
        # bit-correct due do floating point math (exacerbated by exponentials)
        for c in table_new.colnames:
            if c == "Name" or c == "RA" or c == "DEC":
                np.testing.assert_equal(
                    table_normal[c],
                    table_new[c],
                    err_msg="column {} is not equal".format(c),
                )
            else:
                np.testing.assert_allclose(
                    table_normal[c],
                    table_new[c],
                    rtol=tolerance,
                    equal_nan=True,
                    err_msg="column {} is not close enough".format(c),
                )

    def test_beast_settings(self):
        """
        Test that a given text file creates the expected beast_settings class.
        """

        # assert it's the correct class
        assert isinstance(
            self.settings, beast_settings.beast_settings
        ), "Did not produce the correct class"

    def test_compare_spec_type_inFOV(self):
        """
        Test for compare_spec_type.  The spectrally-typed stars aren't real sources,
        they're just invented for the purposes of documenting/testing the code.

        In this version, the stars are in the imaging field of view.
        """
        # run compare_spec_type
        spec_type = compare_spec_type(
            self.obs_fname_cache,
            self.stats_fname_cache,
            [11.2335881, 11.23342557],  # RA
            [41.9001895, 41.90006316],  # Dec
            ["A", "G"],  # Spectral type
            [2, 7],  # Subtype
            ["II", "II"],  # Luminosity class
            match_radius=0.2,  # Match radius (arcsec)
        )

        # expected output table
        expected_table = Table(
            {
                "spec_ra": [11.2335881, 11.23342557],
                "spec_dec": [41.9001895, 41.90006316],
                "spec_type": ["A 2 II", "G 7 II"],
                "spec_teff": [9000.0, 4916.666666666667],
                "spec_logg": [2.7164474106543732, 1.7184474106543735],
                "phot_cat_ind": [27, 8],
                "stats_cat_ind": [27, 8],
                "beast_teff_p50": [9046.250020338754, 4528.230977991138],
                "beast_teff_p16": [8643.670633196869, 4335.617282355577],
                "beast_teff_p84": [9536.391362054928, 4729.401710221546],
                "beast_logg_p50": [2.714286917261312, 1.7684285714285717],
                "beast_logg_p16": [2.636272525730954, 1.7014832653061227],
                "beast_logg_p84": [2.799534708811963, 1.8353738775510207],
                "teff_sigma": [-0.11488422362383206, 1.9308757510045778],
                "logg_sigma": [0.025343687546173433, -0.7465969411324851],
            }
        )

        # compare to new table
        compare_tables(expected_table, Table(spec_type), rtol=2e-3)

    def test_compare_spec_type_notFOV(self):
        """
        Test for compare_spec_type.  The spectrally-typed stars aren't real sources,
        they're just invented for the purposes of documenting/testing the code.

        In this version, the stars are NOT in the imaging field of view.
        """
        # run compare_spec_type
        spec_type = compare_spec_type(
            self.obs_fname_cache,
            self.stats_fname_cache,
            [1.0],  # RA
            [1.0],  # Dec
            ["B"],  # Spectral type
            [4],  # Subtype
            ["V"],  # Luminosity class
            match_radius=0.2,  # Match radius (arcsec)
        )

        # expected output table
        expected_table = Table(
            {
                "spec_ra": [1.0],
                "spec_dec": [1.0],
                "spec_type": ["B 4 V"],
                "spec_teff": [None],
                "spec_logg": [None],
                "phot_cat_ind": [None],
                "stats_cat_ind": [None],
                "beast_teff_p50": [None],
                "beast_teff_p16": [None],
                "beast_teff_p84": [None],
                "beast_logg_p50": [None],
                "beast_logg_p16": [None],
                "beast_logg_p84": [None],
                "teff_sigma": [None],
                "logg_sigma": [None],
            }
        )

        # compare to new table
        compare_tables(expected_table, Table(spec_type))

    def test_star_type_probability_all_params(self):
        """
        Test for star_type_probability.py
        """
        # download the needed files
        star_prob_fname = download_rename("beast_example_phat_startype.fits")

        # run star_type_probability
        star_prob = star_type_probability.star_type_probability(
            self.pdf1d_fname_cache,
            self.pdf2d_fname_cache,
            output_filebase=None,
            ext_O_star_params={"min_M_ini": 10, "min_Av": 0.5, "max_Av": 5},
        )

        # expected output table
        expected_star_prob = Table.read(star_prob_fname)

        # compare to new table
        compare_tables(expected_star_prob, Table(star_prob))

    def test_star_type_probability_no_Av(self):
        """
        Test for star_type_probability.py
        """
        # download the needed files
        pdf2d_fname = download_rename("beast_example_phat_pdf2d_no_Av.fits")
        star_prob_fname = download_rename("beast_example_phat_startype_no_Av.fits")

        # run star_type_probability
        star_prob = star_type_probability.star_type_probability(
            self.pdf1d_fname_cache,
            pdf2d_fname,
            output_filebase=None,
            ext_O_star_params={"min_M_ini": 10, "min_Av": 0.5, "max_Av": 5},
        )

        # expected output table
        expected_star_prob = Table.read(star_prob_fname)

        # compare to new table
        compare_tables(expected_star_prob, Table(star_prob))

    # ###################################################################
    # tools.run tests

    @pytest.mark.usefixtures("setup_create_physicsmodel")
    def test_create_physicsmodel_no_subgrid(self):
        """
        Test create_physicsmodel.py, assuming no subgrids
        """

        # run create_physicsmodel
        create_physicsmodel.create_physicsmodel(
            self.settings, nsubs=self.settings.n_subgrid, nprocs=1
        )

        # check that files match
        # - isochrones
        table_cache = Table.read(
            self.iso_fname_cache, format="ascii.csv", comment="#", delimiter=",",
        )
        table_new = Table.read(
            "./beast_example_phat/beast_example_phat_iso.csv",
            format="ascii.csv",
            comment="#",
            delimiter=",",
        )
        compare_tables(table_cache, table_new)
        # - spectra with priors
        compare_hdf5(
            self.priors_fname_cache,
            "./beast_example_phat/beast_example_phat_spec_w_priors.grid.hd5",
        )
        # - SEDs grid
        compare_hdf5(
            self.seds_fname_cache,
            "./beast_example_phat/beast_example_phat_seds.grid.hd5",
        )

    @pytest.mark.usefixtures("setup_create_physicsmodel")
    def test_create_physicsmodel_with_subgrid(self):
        """
        Test create_physicsmodel.py, assuming two subgrids
        """

        # run create_physicsmodel
        create_physicsmodel.create_physicsmodel(
            self.settings_sg, nsubs=self.settings_sg.n_subgrid, nprocs=1
        )

        # check that files match

        # - isochrones
        table_cache = Table.read(
            self.iso_fname_cache, format="ascii.csv", comment="#", delimiter=",",
        )
        table_new = Table.read(
            "beast_example_phat_subgrids/beast_example_phat_subgrids_iso.csv",
            format="ascii.csv",
            comment="#",
            delimiter=",",
        )
        compare_tables(table_cache, table_new)

        # - spectra with priors
        compare_hdf5(
            self.priors_fname_cache,
            "./beast_example_phat_subgrids/beast_example_phat_subgrids_spec_w_priors.grid.hd5",
        )
        compare_hdf5(
            self.priors_sub0_fname_cache,
            "beast_example_phat_subgrids/beast_example_phat_subgrids_spec_w_priors.gridsub0.hd5",
        )
        compare_hdf5(
            self.priors_sub1_fname_cache,
            "beast_example_phat_subgrids/beast_example_phat_subgrids_spec_w_priors.gridsub1.hd5",
        )

        # - SEDs grid
        compare_hdf5(
            self.seds_sub0_fname_cache,
            "beast_example_phat_subgrids/beast_example_phat_subgrids_seds.gridsub0.hd5",
        )
        compare_hdf5(
            self.seds_sub1_fname_cache,
            "beast_example_phat_subgrids/beast_example_phat_subgrids_seds.gridsub1.hd5",
        )

        # - list of subgrids
        with open("./beast_example_phat_subgrids/subgrid_fnames.txt") as f:
            temp = f.read()
        subgrid_list = [x for x in temp.split("\n") if x != ""]
        expected_list = [
            "beast_example_phat_subgrids/beast_example_phat_subgrids_seds.gridsub0.hd5",
            "beast_example_phat_subgrids/beast_example_phat_subgrids_seds.gridsub1.hd5",
        ]
        assert subgrid_list == expected_list, "subgrid_fnames.txt has incorrect content"

    @pytest.mark.usefixtures("setup_create_obsmodel")
    def test_create_obsmodel_no_subgrid(self):
        """
        Test create_obsmodel.py, assuming no subgrids
        """
        print("running test_create_obsmodel_no_subgrid")

        # run create_obsmodel
        create_obsmodel.create_obsmodel(
            self.settings,
            use_sd=False,
            nsubs=self.settings.n_subgrid,
            nprocs=1,
            use_rate=False,
        )

        # check that files match
        compare_hdf5(
            self.noise_fname_cache,
            "beast_example_phat/beast_example_phat_noisemodel.grid.hd5",
        )

    @pytest.mark.usefixtures("setup_create_obsmodel")
    def test_create_obsmodel_with_subgrid(self):
        """
        Test create_obsmodel.py, assuming two subgrids
        """
        print("running test_create_obsmodel_with_subgrid")

        # run create_obsmodel
        create_obsmodel.create_obsmodel(
            self.settings_sg,
            use_sd=False,
            nsubs=self.settings_sg.n_subgrid,
            nprocs=1,
            use_rate=False,
        )

        # check that files match
        compare_hdf5(
            self.noise_sub0_fname_cache,
            "beast_example_phat_subgrids/beast_example_phat_subgrids_noisemodel.gridsub0.hd5",
        )
        compare_hdf5(
            self.noise_sub1_fname_cache,
            "beast_example_phat_subgrids/beast_example_phat_subgrids_noisemodel.gridsub1.hd5",
        )


# ###################################################################
# specific helper functions


def split_and_check(grid_fname, num_subgrids):
    """
    Split a sed grid into subgrids and test the contents of the subgrids
    are as expected and concatenating the subgrid components (seds, grid)
    gives the full sed grid.

    Parameters
    ----------
    grid_fname : str
        filename for the sed grid

    num_subgrids : int
        number of subgrids to split the sed grid into
    """
    complete_g = SEDGrid(grid_fname)
    sub_fnames = subgridding_tools.split_grid(grid_fname, num_subgrids)

    # count the number of grid cells
    sub_seds = []
    sub_grids = []

    for sub_fname in sub_fnames:
        sub_g = SEDGrid(sub_fname)

        sub_seds.append(sub_g.seds)
        sub_grids.append(sub_g.grid)

        np.testing.assert_equal(complete_g.lamb, sub_g.lamb)
        if not complete_g.grid.colnames == sub_g.grid.colnames:
            raise AssertionError()

    sub_seds_reconstructed = np.concatenate(sub_seds)
    np.testing.assert_equal(sub_seds_reconstructed, complete_g.seds)

    sub_grids_reconstructed = np.concatenate(sub_grids)
    np.testing.assert_equal(sub_grids_reconstructed, complete_g.grid)

    # the split method skips anything that already exists, so if we
    # want to use this function multiple times for the same test
    # grid, we need to do this.
    for f in sub_fnames:
        os.remove(f)


@pytest.fixture(scope="function")
def setup_create_physicsmodel(request):
    """
    Make sure that the folders (and their contents) from the create_physicsmodel
    tests get deleted after the tests run
    """
    # no setup needed

    # run tests
    yield

    # remove folders
    if os.path.isdir("./beast_example_phat"):
        shutil.rmtree("./beast_example_phat")
    if os.path.isdir("./beast_example_phat_subgrids"):
        shutil.rmtree("./beast_example_phat_subgrids")


@pytest.fixture(scope="function")
def setup_create_obsmodel(request):
    """
    Make symlink to files needed for create_obsmodel test so that they're in
    the proper folder.  Delete symlinks after create_obsmodel tests have run.
    """
    # print('setting up files for create_obsmodel')
    # create folders
    os.mkdir("./beast_example_phat")
    os.mkdir("./beast_example_phat_subgrids")
    # make symlinks to SED data
    source_list = [
        request.cls.seds_fname_cache,
        request.cls.seds_sub0_fname_cache,
        request.cls.seds_sub1_fname_cache,
    ]
    dest_list = [
        "./beast_example_phat/beast_example_phat_seds.grid.hd5",
        "./beast_example_phat_subgrids/beast_example_phat_subgrids_seds.gridsub0.hd5",
        "./beast_example_phat_subgrids/beast_example_phat_subgrids_seds.gridsub1.hd5",
    ]
    for source, dest in zip(source_list, dest_list):
        os.symlink(os.path.abspath(source), os.path.abspath(dest))
    # make a subgrid file name list
    with open("./beast_example_phat_subgrids/subgrid_fnames.txt", "w") as f:
        f.write(dest_list[1] + "\n" + dest_list[2] + "\n")

    # run tests
    yield

    # remove folders/symlinks
    # print('teardown for create_obsmodel')
    if os.path.isdir("./beast_example_phat"):
        shutil.rmtree("./beast_example_phat")
    if os.path.isdir("./beast_example_phat_subgrids"):
        shutil.rmtree("./beast_example_phat_subgrids")
