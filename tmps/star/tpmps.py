"""
    TPMPS class, which contains all the neccessary parts for performing time evolution for states represented as
    pmps.
"""

import mpnum as mp
from tmps.star.base.tmpbase import StarTMPBase
from tmps.utils.cform import canonicalize_to
from tmps.utils.compress import compress_pmps_sites, compress_pmps


class StarTPMPS(StarTMPBase):

    def __init__(self, psi_0, propagator, state_compression_kwargs=None, t0=0, psi_0_compression_kwargs=None):
        """
            Constructor for the StarTPMPS class. Time evolution for quantum states represented by purified state (pmps).
            Does required setup steps for evolve to work.
            Uses a pre-constructed MPPropagator object

        :param psi_0: Initial state as MPArray. More precisely as an MPArray with two legs on each site,
                      one physical, one ancilla. Will be normalized before propagation
        :param propagator: Compatible Pre-constructed StarMPPropagator object. (Compatibility means, that the
                           chain shape with which it was constructed matches the shape of psi_0)
        :param state_compression_kwargs: Optional compression kwargs for the state (see real time evolution
                                         factory function for details)
        :param t0: Initial time of the propagation
        :param psi_0_compression_kwargs: Optional compresion kwargs for the initial state (see real time evolution
                                         factory function for details)
        """
        super().__init__(psi_0, propagator, state_compression_kwargs=state_compression_kwargs, t0=t0)
        self.mpa_type = 'pmps'
        self.system_index = propagator.system_index
        self.pmps_compression_step = 1

        # Check if propagator is valid for the selected state mpa_type
        assert self.propagator.ancilla_sites
        assert not self.propagator.build_adj
        # Contract over all indices (physical and ancilla)
        self.axes = ((1, 3), (0, 1))

        self.to_cform = propagator.to_cform
        if psi_0_compression_kwargs is None:
            canonicalize_to(self.psi_t, to_cform=self.to_cform)
        else:
            compress_pmps(self.psi_t, to_cform=self.to_cform, **psi_0_compression_kwargs)

        # Normalize current initial psi_t (=psi_0)
        self.normalize_state()

        # Contains overlap due to compression for the last timestep and for all timesteps combined respectively
        self.last_overlap = 1
        self.cumulative_overlap = 1
        self.base_overlap = 1

        # Contains accumulated trotter errors for each timestep
        self.trotter_error = 0

    def evolve(self):
        """
            Perform a Trotterized time evolution step by tau. Compress after each dot product or after each sweep
            through the chain. Renormalize state after one full timestep due to compression.
        :return:
        """
        overlap = self.base_overlap
        last_start_at = self.system_index
        if not self.canonicalize_every_step:
            canonicalize_to(self.psi_t, to_cform=self.to_cform)
        for (start_at, trotter_mpo) in self.propagator.trotter_steps:
            self.psi_t = mp.partialdot(trotter_mpo, self.psi_t, start_at=start_at, axes=self.axes)
            if self.full_compression:
                if (start_at == self.propagator.L - 2 or start_at == 0) and start_at != last_start_at:
                    # Compress after a full sweep
                    overlap *= self.psi_t.compress(**self.state_compression_kwargs)
            else:
                if len(trotter_mpo) > 1:
                    self.lc.compress(self.psi_t, start_at)
            if (start_at == self.propagator.L - 2 or start_at == 0) and start_at != last_start_at:
                self.pmps_compression_step += 1
                if self.pmps_compression_step-1 == self.compress_sites_step:
                    compress_pmps_sites(self.psi_t, relerr=self.compress_sites_relerr, rank=self.compress_sites_rank,
                                        stable=self.compress_sites_stable, to_cform=self.to_cform)
                    self.pmps_compression_step = 1
            last_start_at = start_at
        if self.final_compression:
            overlap *= self.psi_t.compress(**self.state_compression_kwargs)
        else:
            if not self.canonicalize_every_step:
                canonicalize_to(self.psi_t, to_cform=self.to_cform)
        self._normalize_state()
        self.cumulative_overlap *= overlap
        self.last_overlap = overlap
        if self.system_index == 0:
            # reset by one, because next propagation step starts at the edge of chain and increments counter immediately
            self.pmps_compression_step -= 1
        self.trotter_error += self.propagator.step_trotter_error
        self.stepno += 1

    def _normalize_state(self):
        """
            Normalizes psi_t by dividing by its l2 norm. Internal normalization. Can be overloaded for
            tracking purposes. Is only called during the propagation
        :return:
        """
        self.psi_t /= mp.norm(self.psi_t)

    def normalize_state(self):
        """
            Normalizes psi_t by dividing by its l2 norm.
        :return:
        """
        self.psi_t /= mp.norm(self.psi_t)

    def info(self):
        """
            Returns an info dict which contains information about the current state of the propagation.
            Designed to be returned at the end of a propagation.
            The info dict contains the following keys:
            'ranks'         (ranks of psi at the end of the propagation)
            'overlap'       (cumulative product of overlaps for all compressions),
            'last_overlap'  (product of overlaps for dot products in last propagation step)
            'trotter_error' (estimate of the last cumulative trotter error),
            'op_ranks'      (list which contains the bond dimensions of the trotter operators used for the
                             time evolution and their respective positions in the chain (first element of each tuple))
        :return: info dict
        """
        info = dict()
        info['ranks'] = self.psi_t.ranks
        info['size'] = self.psi_t.size
        info['overlap'] = self.cumulative_overlap
        info['last_overlap'] = self.last_overlap
        info['trotter_error'] = self.trotter_error
        info['op_ranks'] = self.op_ranks
        return info

    def reset(self, psi_0=None, state_compression_kwargs=None, psi_0_compression_kwargs=None):
        """
            Resets selected properties of the TMPO object

            If psi_0 is not None, we set the new initial state to psi_0, otherwise we keep the current state.
            If psi_0 is to be changed, reset overlap and trotter error tracking.
            Checks if shape of passed psi_0 is compatible with the shape of the current propagator object

        :param psi_0: Optional new initial state, need not be normalized. Is normalized before propagation
        :param state_compression_kwargs: Optional. If not None, chamge the current parameters for
                                         state compression. If set None old compression parameters are kept,
                                         if empty, default compression is used. (see real time evolution
                                         factory function for details)
        :param psi_0_compression_kwargs: Optional compression kwargs for the initial state (see real time evolution
                                         factory function for details)
        :return:
        """
        if psi_0 is not None:
            if psi_0_compression_kwargs is None:
                canonicalize_to(psi_0, to_cform=self.to_cform)
            else:
                compress_pmps(psi_0, to_cform=self.to_cform, **psi_0_compression_kwargs)
            # Normalize current initial psi_t (=psi_0)
            self.normalize_state()
            self.pmps_compression_step = 0
            self.last_overlap = 1
            self.cumulative_overlap = 1
            self.base_overlap = 1
            self.trotter_error = 0
            self.stepno = 0
        super().reset(psi_0=psi_0, state_compression_kwargs=state_compression_kwargs)
