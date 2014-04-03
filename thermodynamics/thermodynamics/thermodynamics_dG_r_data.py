from __future__ import with_statement
from math import floor,ceil,log,sqrt,pow,exp,fabs
from copy import deepcopy
from cobra.core.Metabolite import Metabolite
from cobra.core.Reaction import Reaction
from collections import Counter

# Other dependencies
import csv,json,sys

class thermodynamics_dG_r_data():
    """Runs thermodynamic analysis analysis on a cobra.Model object

    #1 Calculate reaction thermodynamics

    measured_concentrations: measured concentration values
                             metabolite.id: {'concentration': float,
                                             'concentration_lb': float,
                                             'concentration_ub': float,
                                             'concentration_var': float,
                                             'concentration_units': string}
                     NOTE: all measured concentrations and variances are computed in ln space (i.e. geometric mean and variance)

    measured_dG_fs: measured or extrapolated compound Gibbs energies of formation
                     metabolite.id: {'dG_f': float,
                                     'dG_f_lb: float,
                                     'dG_f_ub: float,
                                     'dG_f_var': float,
                                     'dG_f_units': string}
                     NOTE: it is assumed a reference of concentration of 1M (implies a pH = 0.0) and ionic strength of 0.0

    estimated_concentrations: estimated concentrations for those metabolites that were not measured
                     metabolite.id: {'concentration': float,
                                     'concentration_lb': float,
                                     'concentration_ub': float,
                                     'concentration_var': float,
                                     'concentration_units': string}

    estimated_dG_fs: estimated compound standard Gibbs energies of formation not measured or extrapolated
                     metabolite.id: {'dG_f': float,
                                     'dG_f_lb: float,
                                     'dG_f_ub: float,
                                     'dG_f_var': float,
                                     'dG_f_units': string

    pH: {metabolite.compartment {'pH': float}}

    temperature: {metabolite.compartment {'temperature': float,
                 'temperature_units': units}}

    ionic strength: {metabolite.compartment {'ionic_strength': float,
                                             'ionic_strength_units': units}}

    returns a dictionary: 
    dG_r = {reaction.id: {'dG_r': float,
                          'dG_r_var': float,
                          'dG_r_lb': float,
                          'dG_r_ub': float,
                          'Keq': float,
                          'ri': float,
                          'ri_estimate': float,
                          'Q': float,
                          'Q_estimate': float,
                          'dG_r_units': string}}

    #2 flux ranges from FVA (same as flux_analysis.variability) or sampling
       and essential reactions from single_reaction_deletion (same as flux_analysis.single_deletion(element_type='reaction'))

    cobra_model: a Model object

    reaction_bounds: {reaction.id: {'flux': float, 'flux_var': float, 'flux_units': string}}

    reaction_deletions: {reaction.id: {'gr': float, 'gr_ratio': float}}
    
    #3 Thermodynamic consistency check based on FVA results

    measured_concentration_coverage_criteria: The fraction of the reaction that is covered
                                              by measured values (defaults > 50%)

    measured_dG_f_coverage_criteria: The fraction of the reaction that is covered
                                              by measured values (defaults > 99%)

    returns a dictionary: 
    thermodynamic_consistency_check = {reaction.id: {'feasible': boolean, NOTE: or None if the below criterion
                                                                    were not met
                                         'measured_concentration_coverage': float,
                                         'measured_dG_r_coverage': float}
    """

    def __init__(self):
        """
        Estimate of reaction bounds:

        for a reaction of the form aA + bB = cC + dD
        dG_r_lb: aA_ub + bB_ub = cC_lb + dD_lb
                R*T*log((C_lb^c + D_lb^d)/(A_ub^a + B_ub^b))
        dG_r_ub: aA_lb + bB_lb = cC_ub + dD_ub
                R*T*log((C_ub^c + D_ub^d)/(A_lb^a + B_lb^b))
        by mixing lb/ub concentration estimations
        (or dG_f)
        we gaurantee that the lb will be < than the ub and
        a true estimate of the range is provided
        """
        
        # initialize constants
        self.R = 8.314e-3; # gas constant 8.3144621 [kJ/K/mol]
        # T = 298.15; # temperature [K] (Not constant b/w compartments)
        self.A = 0.5093; # parameter A of the extended Debye-Huckel equation
                    # [mol^.5*L^-.5]
        self.B = 1.6; # parameter B of the extended Debye-Huckel equation
                 # [mol^.5*L^-.5]
        self.F = 96.485; # faraday's constant [kJ/V/mol]
        self.Rkcal = 1.9858775e-3; # gas constant 8.3144621 [kcal/K/mol]
        self.Fkcal = 23.062e-3; # faraday's constant [kcal/mV/mol]

        # output
        self.dG0_r = {}
        self.dG_r = {}
        self.dG_r_coverage = {}
        self.metabolomics_coverage = {}
        self.thermodynamic_consistency_check = {}

    def export_dG0_r_json(self, filename_I):
        # save the results to json file
        with open(filename_I, 'w') as outfile:
            json.dump(self.dG0_r, outfile, indent=4);

    def export_dG_r_json(self, filename_I):
        # save the results to json file
        with open(filename_I, 'w') as outfile:
            json.dump(self.dG_r, outfile, indent=4);

    def export_tcc_json(self, filename_I):
        # save the results to json file
        with open(filename_I, 'w') as outfile:
            json.dump(self.thermodynamic_consistency_check, outfile, indent=4);

    def calculate_dG0_r(self, cobra_model, measured_dG_f, estimated_dG_f):
        """calculate the standard Gibbs free energy of reaction"""
        # Input:
        #   dG_f (adjusted from dG0_f to in vivo conditions)
        #   thermodynamic_consistency_check
        # Output:
        #   dG0_r
        #   thermodynamic_consistency_check

        """
        a quick primer on the propogation of uncertainty:
        Thanks to Glen Tesler
        http://books.google.com/books?id=csTsQr-v0d0C&pg=PA56

        y = ln(x)
        sigm2y = sigma2x/x
    
        y = R*T*ln(x)
        sigma2y = R*T/x * sigma2x
        """

        dG0_r_I = {};
        dG_r_coverage_I = {}
        for r in cobra_model.reactions:
            dG0_r_I[r.id] = {'dG_r': None,
                           'dG_r_var': None,
                           'dG_r_lb': None,
                           'dG_r_ub': None,
                           'dG_r_units': None};
            dG_r_coverage_I[r.id] = None; # will determine the coverage of thermodynamic
        
            nMets = 0.0; # number of metabolites in each reaction
            nMets_measured = 0.0; # number of measured metabolites in each reaction
            # calculate dG0_r for products
            dG0_r_product = 0.0;
            dG0_r_product_var = 0.0;
            dG0_r_product_lb = 0.0;
            dG0_r_product_ub = 0.0;
            for p in r.products:
                if p.id in measured_dG_f.keys():
                    dG0_r_product = dG0_r_product + measured_dG_f[p.id]['dG_f']*r.get_coefficient(p.id)
                    dG0_r_product_var = dG0_r_product_var + measured_dG_f[p.id]['dG_f_var']
                    # calculate the lb and ub for dG implementation #1
                    dG0_r_product_lb = dG0_r_product_lb + measured_dG_f[p.id]['dG_f_lb']*r.get_coefficient(p.id)
                    dG0_r_product_ub = dG0_r_product_ub + measured_dG_f[p.id]['dG_f_ub']*r.get_coefficient(p.id)
                    ## calculate the lb and ub for dG implementation #2 
                    #dG0_r_product_lb = dG0_r_product_lb + measured_dG_f[p.id]['dG_f_lb']*r.get_coefficient(p.id)
                    #dG0_r_product_ub = dG0_r_product_ub + measured_dG_f[p.id]['dG_f_ub']*r.get_coefficient(p.id)
                    nMets = nMets + 1.0;
                    nMets_measured = nMets_measured + 1.0;
                elif p.id in estimated_dG_f.keys():
                    dG0_r_product = dG0_r_product + estimated_dG_f[p.id]['dG_f']*r.get_coefficient(p.id)
                    dG0_r_product_var = dG0_r_product_var + estimated_dG_f[p.id]['dG_f_var']
                    # calculate the lb and ub for dG implementation #1
                    dG0_r_product_lb = dG0_r_product_lb + estimated_dG_f[p.id]['dG_f_lb']*r.get_coefficient(p.id)
                    dG0_r_product_ub = dG0_r_product_ub + estimated_dG_f[p.id]['dG_f_ub']*r.get_coefficient(p.id)
                    ## calculate the lb and ub for dG implementation #2 
                    #dG0_r_product_lb = dG0_r_product_lb + estimated_dG_f[p.id]['dG_f_lb']*r.get_coefficient(p.id)
                    #dG0_r_product_ub = dG0_r_product_ub + estimated_dG_f[p.id]['dG_f_ub']*r.get_coefficient(p.id)
                    nMets = nMets + 1.0;
                else:
                    # raise error
                    return
            # calculate dG0_r for reactants
            dG0_r_reactant = 0.0;
            dG0_r_reactant_var = 0.0;
            dG0_r_reactant_lb = 0.0;
            dG0_r_reactant_ub = 0.0;
            for react in r.reactants:
                if react.id in measured_dG_f.keys():
                    dG0_r_reactant = dG0_r_reactant + measured_dG_f[react.id]['dG_f']*r.get_coefficient(react.id)
                    dG0_r_reactant_var = dG0_r_reactant_var + measured_dG_f[react.id]['dG_f_var']
                    # calculate the lb and ub for dG implementation #1
                    dG0_r_reactant_lb = dG0_r_reactant_lb + measured_dG_f[react.id]['dG_f_lb']*r.get_coefficient(react.id)
                    dG0_r_reactant_ub = dG0_r_reactant_ub + measured_dG_f[react.id]['dG_f_ub']*r.get_coefficient(react.id)
                    ## calculate the lb and ub for dG implementation #2
                    #dG0_r_reactant_lb = dG0_r_reactant_lb + measured_dG_f[react.id]['dG_f_ub']*r.get_coefficient(react.id)
                    #dG0_r_reactant_ub = dG0_r_reactant_ub + measured_dG_f[react.id]['dG_f_lb']*r.get_coefficient(react.id)
                    nMets = nMets + 1.0;
                    nMets_measured = nMets_measured + 1.0;
                elif react.id in estimated_dG_f.keys():
                    dG0_r_reactant = dG0_r_reactant + estimated_dG_f[react.id]['dG_f']*r.get_coefficient(react.id)
                    dG0_r_reactant_var = dG0_r_reactant_var + estimated_dG_f[react.id]['dG_f_var']
                    # calculate the lb and ub for dG implementation #1
                    dG0_r_reactant_lb = dG0_r_reactant_lb + estimated_dG_f[react.id]['dG_f_lb']*r.get_coefficient(react.id)
                    dG0_r_reactant_ub = dG0_r_reactant_ub + estimated_dG_f[react.id]['dG_f_ub']*r.get_coefficient(react.id)
                    ## calculate the lb and ub for dG implementation #2
                    #dG0_r_reactant_lb = dG0_r_reactant_lb + estimated_dG_f[react.id]['dG_f_ub']*r.get_coefficient(react.id)
                    #dG0_r_reactant_ub = dG0_r_reactant_ub + estimated_dG_f[react.id]['dG_f_lb']*r.get_coefficient(react.id)
                    nMets = nMets + 1.0;
                else:
                    # raise error
                    return
            # calculate the dG0_r for the reaction
            dG0_r_I[r.id]['dG_r'] = dG0_r_product + dG0_r_reactant;
            dG0_r_I[r.id]['dG_r_var'] = dG0_r_product_var + dG0_r_reactant_var;
            # implementation 1 to calculate the bounds
            #dG0_r_I[r.id]['dG_r_lb'] = dG0_r_product + dG0_r_reactant - sqrt(dG0_r_product_var + dG0_r_reactant_var);
            #dG0_r_I[r.id]['dG_r_ub'] = dG0_r_product + dG0_r_reactant + sqrt(dG0_r_product_var + dG0_r_reactant_var);
            # implementation 2 to calculate the bounds
            dG0_r_I[r.id]['dG_r_lb'] = dG0_r_product_lb + dG0_r_reactant_lb;
            dG0_r_I[r.id]['dG_r_ub'] = dG0_r_product_ub + dG0_r_reactant_ub;
            dG0_r_I[r.id]['dG_r_units'] = 'kJ/mol';

            # determine the thermodynamic coverage
            if nMets == 0:  dG_r_coverage_tmp = 0;
            else: dG_r_coverage_tmp = nMets_measured/nMets
            dG_r_coverage_I[r.id] = dG_r_coverage_tmp;

        self.dG0_r = dG0_r_I;
        self.dG_r_coverage = dG_r_coverage_I;

    def calculate_dG_r(self, cobra_model, measured_concentration, estimated_concentration,
                           pH, ionic_strength, temperature):
        """calculate the Gibbs free energy of reaction accounting for the following:
        metabolite concentrations
        pH (proton concentration)
        temperature: accounted for in dG_f
        change in ionic_strength: accounted for in dG_f
        transport processes (membrane potential and  proton exchange)"""
        # Input:
        #   measured_concentrations
        #   estimated_concentrations
        #   dG0_r
        #   thermodynamic_consistency_check
        #   temperature
        #   pH
        #   ionic_strength
        # Output:
        #   dG_r
        #   thermodynamic_consistency_check

        # NOTES:
        #   The lower and upper bounds for dG_r can be calculated in two ways:
        #   #1 provides a more conservative estimate, but can often reverse lb/ub dG_r values
        #       depending on how wide the lb/ub is for reactants and products (default)
        #   #2 provides a less conservative estimate, but avoids reversing the lb/ub dG_r values
        #       even when the lb/ub for reactants and products is wide
    
        # initialize hydrogens:
        hydrogens = [];
        compartments = list(set(cobra_model.metabolites.list_attr('compartment')));
        for compart in compartments:
             hydrogens.append('h_' + compart);

        dG_r_I = {};
        metabolomics_coverage_I = {};
        for r in cobra_model.reactions:
            dG_r_I[r.id] = {'dG_r': None,
                           'dG_r_var': None,
                           'dG_r_units': None,
                           'dG_r_lb': None,
                           'dG_r_ub': None
                           #'Keq': None,
                           #'ri_estimate': None,
                           #'ri': None,
                           #'Q_estimate': None,
                           #'Q': None
                           };

            metabolomics_coverage_I[r.id] = None; # will determine the coverage of metabolomics data

            nMets = 0.0; # number of metabolites in each reaction
            nMets_measured = 0.0; # number of measured metabolites in each reaction

            temperatures = [];

            dG_r_ionic = 0.0; # adjustment for ionic strength (Henry et al, 2007, Biophysical Journal 92(5) 1792?1805)

            # adjustment for transport reactions (Henry et al, 2007, Biophysical Journal 92(5) 1792?1805)
            mets_trans = self.find_transportMets(cobra_model,r.id);
            d_h = 0; # change in proton transfer accross the membrane
            compartments_charge = {}; # compartments of the products {metabolite.compartment:{metabolite.id:metabolite.charge}}
            dG_r_mem = 0.0; # change in membrane potential
            dG_r_pH = 0.0; # adjustment for pH (Robert A. Alberty, Thermodynamics of biochemical reactions (Hoboken N.J.: Wiley-Interscience, 2003).
            dG_r_trans = 0.0;

            # calculate dG_r for products
            dG_r_product = 0.0;
            dG_r_product_var = 0.0;
            dG_r_product_lb = 0.0;
            dG_r_product_ub = 0.0;
            for p in r.products:
                if not(p.id in hydrogens): # exclude hydrogen because it has already been accounted for when adjusting for the pH
                    if p.id in measured_concentration.keys():
                        # calculate the dG_r of the reactants using measured concentrations
                        #   NOTE: since the geometric mean is linear with respect to dG, no adjustments needs to be made
                        dG_r_product = dG_r_product + self.R*temperature[p.compartment]['temperature']*\
                                                            log(measured_concentration[p.id]['concentration'])*r.get_coefficient(p.id);
                        # calculate the variance contributed to dG_r by the uncertainty in the measured concentrations
                        #   NOTE: provides an estimate only...
                        #         i.e. improvements need to be made
                        #dG_r_product_var = dG_r_product_var + pow(self.R*temperature[p.compartment]['temperature']*fabs(r.get_coefficient(p.id)),2)/measured_concentration[p.id]['concentration']*measured_concentration[p.id]['concentration_var'];
                        #dG_r_product_var = dG_r_product_var + exp(self.R*temperature[p.compartment]['temperature']*fabs(r.get_coefficient(p.id))/log(measured_concentration[p.id]['concentration'])*log(measured_concentration[p.id]['concentration_var']));
                    
                        # calculate the lb and ub for dG implementation #1
                        dG_r_product_lb = dG_r_product_lb + self.R*temperature[p.compartment]['temperature']*\
                                                            log(measured_concentration[p.id]['concentration_lb'])*r.get_coefficient(p.id);
                        dG_r_product_ub = dG_r_product_ub + self.R*temperature[react.compartment]['temperature']*\
                                                            log(measured_concentration[p.id]['concentration_ub'])*r.get_coefficient(p.id);
                        ## calculate the lb and ub for dG implementation #2
                        #dG_r_product_lb = dG_r_product_lb + self.R*temperature[p.compartment]['temperature']*\
                        #                                    log(measured_concentration[p.id]['concentration_lb'])*r.get_coefficient(p.id);
                        #dG_r_product_ub = dG_r_product_ub + self.R*temperature[react.compartment]['temperature']*\
                        #                                    log(measured_concentration[p.id]['concentration_ub'])*r.get_coefficient(p.id);

                        # calculate the contribution of charge transfer accross the membrane to dG_r
                        #   NOTE: dG_r_mem = c*F*deltaPsi = c*F*(33.33*deltaPH-143.33)
                        #           where c = net charge transport
                        #                 F = Faradays constant
                        #                 deltaPSI = electrochemical potential
                        #                 deltaPH = pH gradient
                        #   for transport reactions involving the movement of reactant to product
                        #           of the form a*produc = b*react, the equation
                        #           can be broken into two parts: 1 for product and 1 for reactant  
                        #           each with 1/2 the contribution to the net reactions
                        #         dG_r_mem = dG_r_mem_prod + dG_r_mem_react
                        #           where dG_r_mem_prod = abs(a)/2*charge_prod/2*F(33.3*sign(a)*pH_comp_prod-143.33/2)
                        #                 dG_r_mem_react = abs(b)/2*charge_react/2*F(33.3*sign(b)*pH_comp_react-143.33/2)
                    
                        # calculate the contribution of charge transfer accross the membrane to dG_r
                        if p.name in mets_trans: dG_r_mem += fabs(r.get_coefficient(p.id))/2.0*p.charge/2.0*self.F*(33.3*r.get_coefficient(p.id)/fabs(r.get_coefficient(p.id))*pH[p.compartment]['pH']-143.33/2.0);

                        nMets = nMets + 1.0;
                        nMets_measured = nMets_measured + 1.0;

                        #dG_r_ionic = dG_r_ionic - log(10)*self.R*temperature[p.compartment]['temperature']*A*\
                        #                (p.charge*p.charge*r.get_coefficient(p.id)*ionic_strength[p.compartment]['ionic_strength'])/(1+B*ionic_strength[p.compartment]['ionic_strength']);
                        #dG_r_pH = dG_r_pH + log(10)*self.R*temperature[p.compartment]['temperature']*p.charge*pH[p.compartment]['pH'];

                    elif p.id in estimated_concentration.keys():
                        # calculate the dG_r of the reactants using estimated concentrations
                        #   NOTE: since the geometric mean is linear with respect to dG, no adjustments needs to be made
                        dG_r_product = dG_r_product + self.R*temperature[p.compartment]['temperature']*\
                                                            log(estimated_concentration[p.id]['concentration'])*r.get_coefficient(p.id);
                        # calculate the variance contributed to dG_r by the uncertainty in the measured concentrations
                        #   NOTE: provides an estimate only...
                        #         i.e. improvements need to be made
                        #dG_r_product_var = dG_r_product_var + self.R*temperature[p.compartment]['temperature']*fabs(r.get_coefficient(p.id))/estimated_concentration[p.id]['concentration']*\
                        #                                        estimated_concentration[p.id]['concentration_var'];
                        #dG_r_product_var = dG_r_product_var + exp(self.R*temperature[p.compartment]['temperature']*fabs(r.get_coefficient(p.id))/log(estimated_concentration[p.id]['concentration'])*\
                        #                                        log(estimated_concentration[p.id]['concentration_var']));

                        # calculate the lb and ub for dG implementation #1
                        dG_r_product_lb = dG_r_product_lb + self.R*temperature[p.compartment]['temperature']*\
                                                            log(estimated_concentration[p.id]['concentration_lb'])*r.get_coefficient(p.id);
                        dG_r_product_ub = dG_r_product_ub + self.R*temperature[p.compartment]['temperature']*\
                                                            log(estimated_concentration[p.id]['concentration_ub'])*r.get_coefficient(p.id);
                        ## calculate the lb and ub for dG implementation #2
                        #dG_r_product_lb = dG_r_product_lb + self.R*temperature[p.compartment]['temperature']*\
                        #                                    log(estimated_concentration[p.id]['concentration_lb'])*r.get_coefficient(p.id);
                        #dG_r_product_ub = dG_r_product_ub + self.R*temperature[p.compartment]['temperature']*\
                        #                                    log(estimated_concentration[p.id]['concentration_ub'])*r.get_coefficient(p.id);
                    
                        # calculate the contribution of charge transfer accross the membrane to dG_r
                        if p.name in mets_trans: dG_r_mem += fabs(r.get_coefficient(p.id))/2.0*p.charge/2.0*self.F*(33.3*r.get_coefficient(p.id)/fabs(r.get_coefficient(p.id))*pH[p.compartment]['pH']-143.33/2.0);

                        nMets = nMets + 1.0;
                        #dG_r_ionic = dG_r_ionic - log(10)*self.R*temperature[p.compartment]['temperature']*A*\
                        #                (p.charge*p.charge*r.get_coefficient(p.id)*ionic_strength[p.compartment]['ionic_strength'])/(1+B*ionic_strength[p.compartment]['ionic_strength']);
                        #dG_r_pH = dG_r_pH + log(10)*self.R*temperature[p.compartment]['temperature']*p.charge*pH[p.compartment]['pH'];

                    else:
                        # raise error
                        return
                else:
                    if p.name in mets_trans: 
                        dG_r_mem += fabs(r.get_coefficient(p.id))/2.0*p.charge/2.0*self.F*(33.3*r.get_coefficient(p.id)/fabs(r.get_coefficient(p.id))*pH[p.compartment]['pH']-143.33/2.0);
                        dG_r_pH = dG_r_pH - log(10)*self.R*temperature[p.compartment]['temperature']*p.charge*pH[p.compartment]['pH']*r.get_coefficient(p.id)/2.0;

                temperatures.append(temperature[p.compartment]['temperature']);
            # calculate dG_r for reactants
            dG_r_reactant = 0.0;
            dG_r_reactant_var = 0.0;
            dG_r_reactant_lb = 0.0;
            dG_r_reactant_ub = 0.0;
            for react in r.reactants:
                if not(react.id in hydrogens): # exclude hydrogen because it has already been accounted for when adjusting for the pH
                    if react.id in measured_concentration.keys():
                        # calculate the dG_r of the reactants using measured concentrations
                        #   NOTE: since the geometric mean is linear with respect to dG, no adjustments needs to be made
                        dG_r_reactant = dG_r_reactant + self.R*temperature[react.compartment]['temperature']*\
                                                            log(measured_concentration[react.id]['concentration'])*r.get_coefficient(react.id);
                        # calculate the variance contributed to dG_r by the uncertainty in the measured concentrations
                        #   NOTE: provides an estimate only...
                        #         i.e. improvements need to be made
                        #dG_r_reactant_var = dG_r_reactant_var + self.R*temperature[react.compartment]['temperature']*fabs(r.get_coefficient(react.id))/measured_concentration[react.id]['concentration']*measured_concentration[react.id]['concentration_var'];
                        #dG_r_reactant_var = dG_r_reactant_var + exp(self.R*temperature[react.compartment]['temperature']*fabs(r.get_coefficient(react.id))/log(measured_concentration[react.id]['concentration'])*log(measured_concentration[react.id]['concentration_var']));
                    
                        # calculate the lb and ub for dG implementation #1
                        dG_r_reactant_lb = dG_r_reactant_lb + self.R*temperature[react.compartment]['temperature']*\
                                                            log(measured_concentration[react.id]['concentration_lb'])*r.get_coefficient(react.id);
                        dG_r_reactant_ub = dG_r_reactant_ub + self.R*temperature[react.compartment]['temperature']*\
                                                            log(measured_concentration[react.id]['concentration_ub'])*r.get_coefficient(react.id);
                        ## calculate the lb and ub for dG implementation #2
                        #dG_r_reactant_lb = dG_r_reactant_lb + self.R*temperature[react.compartment]['temperature']*\
                        #                                    log(measured_concentration[react.id]['concentration_ub'])*r.get_coefficient(react.id);
                        #dG_r_reactant_ub = dG_r_reactant_ub + self.R*temperature[react.compartment]['temperature']*\
                        #                                    log(measured_concentration[react.id]['concentration_lb'])*r.get_coefficient(react.id);

                        # calculate the contribution of charge transfer accross the membrane to dG_r
                        #   NOTE: dG_r_mem = c*F*deltaPsi = c*F*(33.33*deltaPH-143.33)
                        #           where c = net charge transport
                        #                 F = Faradays constant
                        #                 deltaPSI = electrochemical potential
                        #                 deltaPH = pH gradient
                        #   for transport reactions involving the movement of reactant to product
                        #           of the form a*produc = b*react, the equation
                        #           can be broken into two parts: 1 for product and 1 for reactant  
                        #           each with 1/2 the contribution to the net reactions
                        #         dG_r_mem = dG_r_mem_prod + dG_r_mem_react
                        #           where dG_r_mem_prod = abs(a)/2*charge_prod/2*F(33.3*sign(a)*pH_comp_prod-143.33/2)
                        #                 dG_r_mem_react = abs(b)/2*charge_react/2*F(33.3*sign(b)*pH_comp_react-143.33/2)
                        if react.name in mets_trans: dG_r_mem += fabs(r.get_coefficient(react.id))/2.0*react.charge/2.0*self.F*(33.3*r.get_coefficient(react.id)/fabs(r.get_coefficient(react.id))*pH[react.compartment]['pH']-143.33/2.0);

                        nMets = nMets + 1.0;
                        nMets_measured = nMets_measured + 1.0;

                        #dG_r_ionic = dG_r_ionic - log(10)*self.R*temperature[react.compartment]['temperature']*A*\
                        #                (react.charge*react.charge*r.get_coefficient(react.id)*ionic_strength[react.compartment]['ionic_strength'])/(1+B*ionic_strength[react.compartment]['ionic_strength']);
                        #dG_r_pH = dG_r_pH + log(10)*self.R*temperature[react.compartment]['temperature']*react.charge*pH[react.compartment]['pH']*r.get_coefficient(react.id);

                    elif react.id in estimated_concentration.keys():
                        # calculate the dG_r of the reactants using estimated concentrations
                        #   NOTE: since the geometric mean is linear with respect to dG, no adjustments needs to be made
                        dG_r_reactant = dG_r_reactant + self.R*temperature[react.compartment]['temperature']*\
                                                            log(estimated_concentration[react.id]['concentration'])*r.get_coefficient(react.id);
                        # calculate the variance contributed to dG_r by the uncertainty in the measured concentrations
                        #   NOTE: provides an estimate only...
                        #         i.e. improvements need to be made
                        #dG_r_reactant_var = dG_r_reactant_var + self.R*temperature[react.compartment]['temperature']*fabs(r.get_coefficient(react.id))/estimated_concentration[react.id]['concentration']*\
                        #                                        estimated_concentration[react.id]['concentration_var'];
                        #dG_r_reactant_var = dG_r_reactant_var + exp(self.R*temperature[react.compartment]['temperature']*fabs(r.get_coefficient(react.id))/log(estimated_concentration[react.id]['concentration'])*\
                        #                                        log(estimated_concentration[react.id]['concentration_var']));
                    
                        # calculate the lb and ub for dG implementation #1
                        dG_r_reactant_lb = dG_r_reactant_lb + self.R*temperature[react.compartment]['temperature']*\
                                                            log(estimated_concentration[react.id]['concentration_lb'])*r.get_coefficient(react.id);
                        dG_r_reactant_ub = dG_r_reactant_ub + self.R*temperature[react.compartment]['temperature']*\
                                                            log(estimated_concentration[react.id]['concentration_ub'])*r.get_coefficient(react.id);
                        ## calculate the lb and ub for dG implementation #2
                        #dG_r_reactant_lb = dG_r_reactant_lb + self.R*temperature[react.compartment]['temperature']*\
                        #                                    log(estimated_concentration[react.id]['concentration_ub'])*r.get_coefficient(react.id);
                        #dG_r_reactant_ub = dG_r_reactant_ub + self.R*temperature[react.compartment]['temperature']*\
                        #                                    log(estimated_concentration[react.id]['concentration_lb'])*r.get_coefficient(react.id);
                    
                        # calculate the contribution of charge transfer accross the membrane to dG_r
                        if react.name in mets_trans: dG_r_mem += fabs(r.get_coefficient(react.id))/2.0*react.charge/2.0*self.F*(33.3*r.get_coefficient(react.id)/fabs(r.get_coefficient(react.id))*pH[react.compartment]['pH']-143.33/2.0);

                        nMets = nMets + 1.0;
                        #dG_r_ionic = dG_r_ionic - log(10)*self.R*temperature[react.compartment]['temperature']*A*\
                        #                (react.charge*react.charge*r.get_coefficient(react.id)*ionic_strength[react.compartment]['ionic_strength'])/(1+B*ionic_strength[react.compartment]['ionic_strength']);
                    else:
                        # raise error
                        return
                else: 
                    if react.name in mets_trans: 
                        dG_r_mem += fabs(r.get_coefficient(react.id))/2.0*react.charge/2.0*self.F*(33.3*r.get_coefficient(react.id)/fabs(r.get_coefficient(react.id))*pH[react.compartment]['pH']-143.33/2.0);
                        dG_r_pH = dG_r_pH + log(10)*self.R*temperature[react.compartment]['temperature']*pH[react.compartment]['pH']*r.get_coefficient(react.id)/2.0;
                temperatures.append(temperature[react.compartment]['temperature']);

            # adjustment for transport reactions:
            dG_r_trans = dG_r_mem + dG_r_pH;

            # calculate the dG_r for the reaction
            dG_r_I[r.id]['dG_r'] = self.dG0_r[r.id]['dG_r'] + dG_r_product + dG_r_reactant + dG_r_trans;
            dG_r_I[r.id]['dG_r_var'] = self.dG0_r[r.id]['dG_r_var'] + dG_r_product_var + dG_r_reactant_var;
            dG_r_I[r.id]['dG_r_lb'] = self.dG0_r[r.id]['dG_r_lb'] + dG_r_product_lb + dG_r_reactant_lb + dG_r_trans;
            dG_r_I[r.id]['dG_r_ub'] = self.dG0_r[r.id]['dG_r_ub'] + dG_r_product_ub + dG_r_reactant_ub + dG_r_trans;
            dG_r_I[r.id]['dG_r_units'] = 'kJ/mol';

            ## not the best way to calculate the "in vivo" Keq
            #Keq_exp = -self.dG0_r[r.id]['dG_r']/(sum(temperatures)/len(temperatures)*self.R);
            #if Keq_exp>100: Keq_exp = 100;
            #dG_r_I[r.id]['Keq'] = exp(Keq_exp);

            # determine the thermodynamic coverage
            if nMets == 0:  conc_coverage = 0;
            else: conc_coverage = nMets_measured/nMets
            metabolomics_coverage_I[r.id] = conc_coverage;

        self.dG_r = dG_r_I;
        self.metabolomics_coverage = metabolomics_coverage_I;

    def calculate_dG0_r_v2(self, cobra_model, measured_dG_f, estimated_dG_f):
        """calculate the standard Gibbs free energy of reaction"""
        # Input:
        #   dG_f (adjusted from dG0_f to in vivo conditions)
        #   thermodynamic_consistency_check
        # Output:
        #   dG0_r
        #   thermodynamic_consistency_check

        """
        a quick primer on the propogation of uncertainty:
        Thanks to Glen Tesler
        http://books.google.com/books?id=csTsQr-v0d0C&pg=PA56

        y = ln(x)
        sigm2y = sigma2x/x
    
        y = R*T*ln(x)
        sigma2y = R*T/x * sigma2x
        """

        dG0_r_I = {};
        dG_r_coverage_I = {}
        for r in cobra_model.reactions:
            dG0_r_I[r.id] = {'dG_r': None,
                           'dG_r_var': None,
                           'dG_r_lb': None,
                           'dG_r_ub': None,
                           'dG_r_units': None};
            dG_r_coverage_I[r.id] = None; # will determine the coverage of thermodynamic
        
            nMets = 0.0; # number of metabolites in each reaction
            nMets_measured = 0.0; # number of measured metabolites in each reaction
            # calculate dG0_r for products
            dG0_r_product = 0.0;
            dG0_r_product_var = 0.0;
            dG0_r_product_lb = 0.0;
            dG0_r_product_ub = 0.0;
            for p in r.products:
                if p.id in measured_dG_f.keys():
                    dG0_r_product = dG0_r_product + measured_dG_f[p.id]['dG_f']*r.get_coefficient(p.id)
                    dG0_r_product_var = dG0_r_product_var + measured_dG_f[p.id]['dG_f_var']
                    ## calculate the lb and ub for dG implementation #1
                    #dG0_r_product_lb = dG0_r_product_lb + measured_dG_f[p.id]['dG_f_lb']*r.get_coefficient(p.id)
                    #dG0_r_product_ub = dG0_r_product_ub + measured_dG_f[p.id]['dG_f_ub']*r.get_coefficient(p.id)
                    # calculate the lb and ub for dG implementation #2 
                    dG0_r_product_lb = dG0_r_product_lb + measured_dG_f[p.id]['dG_f_lb']*r.get_coefficient(p.id)
                    dG0_r_product_ub = dG0_r_product_ub + measured_dG_f[p.id]['dG_f_ub']*r.get_coefficient(p.id)
                    nMets = nMets + 1.0;
                    nMets_measured = nMets_measured + 1.0;
                elif p.id in estimated_dG_f.keys():
                    dG0_r_product = dG0_r_product + estimated_dG_f[p.id]['dG_f']*r.get_coefficient(p.id)
                    #dG0_r_product_var = dG0_r_product_var + estimated_dG_f[p.id]['dG_f_var']
                    ## calculate the lb and ub for dG implementation #1
                    #dG0_r_product_lb = dG0_r_product_lb + estimated_dG_f[p.id]['dG_f_lb']*r.get_coefficient(p.id)
                    #dG0_r_product_ub = dG0_r_product_ub + estimated_dG_f[p.id]['dG_f_ub']*r.get_coefficient(p.id)
                    # calculate the lb and ub for dG implementation #2 
                    dG0_r_product_lb = dG0_r_product_lb + estimated_dG_f[p.id]['dG_f_lb']*r.get_coefficient(p.id)
                    dG0_r_product_ub = dG0_r_product_ub + estimated_dG_f[p.id]['dG_f_ub']*r.get_coefficient(p.id)
                    nMets = nMets + 1.0;
                else:
                    # raise error
                    return
            # calculate dG0_r for reactants
            dG0_r_reactant = 0.0;
            dG0_r_reactant_var = 0.0;
            dG0_r_reactant_lb = 0.0;
            dG0_r_reactant_ub = 0.0;
            for react in r.reactants:
                if react.id in measured_dG_f.keys():
                    dG0_r_reactant = dG0_r_reactant + measured_dG_f[react.id]['dG_f']*r.get_coefficient(react.id)
                    dG0_r_reactant_var = dG0_r_reactant_var + measured_dG_f[react.id]['dG_f_var']
                    ## calculate the lb and ub for dG implementation #1
                    #dG0_r_reactant_lb = dG0_r_reactant_lb + measured_dG_f[react.id]['dG_f_lb']*r.get_coefficient(react.id)
                    #dG0_r_reactant_ub = dG0_r_reactant_ub + measured_dG_f[react.id]['dG_f_ub']*r.get_coefficient(react.id)
                    # calculate the lb and ub for dG implementation #2
                    dG0_r_reactant_lb = dG0_r_reactant_lb + measured_dG_f[react.id]['dG_f_ub']*r.get_coefficient(react.id)
                    dG0_r_reactant_ub = dG0_r_reactant_ub + measured_dG_f[react.id]['dG_f_lb']*r.get_coefficient(react.id)
                    nMets = nMets + 1.0;
                    nMets_measured = nMets_measured + 1.0;
                elif react.id in estimated_dG_f.keys():
                    dG0_r_reactant = dG0_r_reactant + estimated_dG_f[react.id]['dG_f']*r.get_coefficient(react.id)
                    dG0_r_reactant_var = dG0_r_reactant_var + estimated_dG_f[react.id]['dG_f_var']
                    ## calculate the lb and ub for dG implementation #1
                    #dG0_r_reactant_lb = dG0_r_reactant_lb + estimated_dG_f[react.id]['dG_f_lb']*r.get_coefficient(react.id)
                    #dG0_r_reactant_ub = dG0_r_reactant_ub + estimated_dG_f[react.id]['dG_f_ub']*r.get_coefficient(react.id)
                    # calculate the lb and ub for dG implementation #2
                    dG0_r_reactant_lb = dG0_r_reactant_lb + estimated_dG_f[react.id]['dG_f_ub']*r.get_coefficient(react.id)
                    dG0_r_reactant_ub = dG0_r_reactant_ub + estimated_dG_f[react.id]['dG_f_lb']*r.get_coefficient(react.id)
                    nMets = nMets + 1.0;
                else:
                    # raise error
                    return
            # calculate the dG0_r for the reaction
            dG0_r_I[r.id]['dG_r'] = dG0_r_product + dG0_r_reactant;
            dG0_r_I[r.id]['dG_r_var'] = dG0_r_product_var + dG0_r_reactant_var;
            # implementation 1 to calculate the bounds
            #dG0_r_I[r.id]['dG_r_lb'] = dG0_r_product + dG0_r_reactant - sqrt(dG0_r_product_var + dG0_r_reactant_var);
            #dG0_r_I[r.id]['dG_r_ub'] = dG0_r_product + dG0_r_reactant + sqrt(dG0_r_product_var + dG0_r_reactant_var);
            # implementation 2 to calculate the bounds
            dG0_r_I[r.id]['dG_r_lb'] = dG0_r_product_lb + dG0_r_reactant_lb;
            dG0_r_I[r.id]['dG_r_ub'] = dG0_r_product_ub + dG0_r_reactant_ub;
            dG0_r_I[r.id]['dG_r_units'] = 'kJ/mol';

            # determine the thermodynamic coverage
            if nMets == 0:  dG_r_coverage_tmp = 0;
            else: dG_r_coverage_tmp = nMets_measured/nMets
            dG_r_coverage_I[r.id] = dG_r_coverage_tmp;

        self.dG0_r = dG0_r_I;
        self.dG_r_coverage = dG_r_coverage_I;

    def calculate_dG_r_v2(self, cobra_model, measured_concentration, estimated_concentration,
                           pH, ionic_strength, temperature):
        """calculate the Gibbs free energy of reaction accounting for the following:
        metabolite concentrations
        pH (proton concentration)
        temperature: accounted for in dG_f
        change in ionic_strength: accounted for in dG_f
        transport processes (membrane potential and  proton exchange)"""
        # Input:
        #   measured_concentrations
        #   estimated_concentrations
        #   dG0_r
        #   thermodynamic_consistency_check
        #   temperature
        #   pH
        #   ionic_strength
        # Output:
        #   dG_r
        #   thermodynamic_consistency_check

        # NOTES:
        #   The lower and upper bounds for dG_r can be calculated in two ways:
        #   #1 provides a more conservative estimate, but can often reverse lb/ub dG_r values
        #       depending on how wide the lb/ub is for reactants and products (default)
        #   #2 provides a less conservative estimate, but avoids reversing the lb/ub dG_r values
        #       even when the lb/ub for reactants and products is wide
    
        # initialize hydrogens:
        hydrogens = [];
        compartments = list(set(cobra_model.metabolites.list_attr('compartment')));
        for compart in compartments:
             hydrogens.append('h_' + compart);

        dG_r_I = {};
        metabolomics_coverage_I = {};
        for r in cobra_model.reactions:
            dG_r_I[r.id] = {'dG_r': None,
                           'dG_r_var': None,
                           'dG_r_units': None,
                           'dG_r_lb': None,
                           'dG_r_ub': None
                           #'Keq': None,
                           #'ri_estimate': None,
                           #'ri': None,
                           #'Q_estimate': None,
                           #'Q': None
                           };

            metabolomics_coverage_I[r.id] = None; # will determine the coverage of metabolomics data

            nMets = 0.0; # number of metabolites in each reaction
            nMets_measured = 0.0; # number of measured metabolites in each reaction

            temperatures = [];

            dG_r_ionic = 0.0; # adjustment for ionic strength (Henry et al, 2007, Biophysical Journal 92(5) 1792?1805)

            # adjustment for transport reactions (Henry et al, 2007, Biophysical Journal 92(5) 1792?1805)
            mets_trans = self.find_transportMets(cobra_model,r.id);
            d_h = 0; # change in proton transfer accross the membrane
            compartments_charge = {}; # compartments of the products {metabolite.compartment:{metabolite.id:metabolite.charge}}
            dG_r_mem = 0.0; # change in membrane potential
            dG_r_pH = 0.0; # adjustment for pH (Robert A. Alberty, Thermodynamics of biochemical reactions (Hoboken N.J.: Wiley-Interscience, 2003).
            dG_r_trans = 0.0;

            # calculate dG_r for products
            dG_r_product = 0.0;
            dG_r_product_var = 0.0;
            dG_r_product_lb = 0.0;
            dG_r_product_ub = 0.0;
            for p in r.products:
                if not(p.id in hydrogens): # exclude hydrogen because it has already been accounted for when adjusting for the pH
                    if p.id in measured_concentration.keys():
                        # calculate the dG_r of the reactants using measured concentrations
                        #   NOTE: since the geometric mean is linear with respect to dG, no adjustments needs to be made
                        dG_r_product = dG_r_product + self.R*temperature[p.compartment]['temperature']*\
                                                            log(measured_concentration[p.id]['concentration'])*r.get_coefficient(p.id);
                        # calculate the variance contributed to dG_r by the uncertainty in the measured concentrations
                        #   NOTE: provides an estimate only...
                        #         i.e. improvements need to be made
                        #dG_r_product_var = dG_r_product_var + pow(self.R*temperature[p.compartment]['temperature']*fabs(r.get_coefficient(p.id)),2)/measured_concentration[p.id]['concentration']*measured_concentration[p.id]['concentration_var'];
                        #dG_r_product_var = dG_r_product_var + exp(self.R*temperature[p.compartment]['temperature']*fabs(r.get_coefficient(p.id))/log(measured_concentration[p.id]['concentration'])*log(measured_concentration[p.id]['concentration_var']));
                    
                        ## calculate the lb and ub for dG implementation #1
                        #dG_r_product_lb = dG_r_product_lb + self.R*temperature[p.compartment]['temperature']*\
                        #                                    log(measured_concentration[p.id]['concentration_lb'])*r.get_coefficient(p.id);
                        #dG_r_product_ub = dG_r_product_ub + self.R*temperature[react.compartment]['temperature']*\
                        #                                    log(measured_concentration[p.id]['concentration_ub'])*r.get_coefficient(p.id);
                        # calculate the lb and ub for dG implementation #2
                        dG_r_product_lb = dG_r_product_lb + self.R*temperature[p.compartment]['temperature']*\
                                                            log(measured_concentration[p.id]['concentration_lb'])*r.get_coefficient(p.id);
                        dG_r_product_ub = dG_r_product_ub + self.R*temperature[react.compartment]['temperature']*\
                                                            log(measured_concentration[p.id]['concentration_ub'])*r.get_coefficient(p.id);

                        # calculate the contribution of charge transfer accross the membrane to dG_r
                        #   NOTE: dG_r_mem = c*F*deltaPsi = c*F*(33.33*deltaPH-143.33)
                        #           where c = net charge transport
                        #                 F = Faradays constant
                        #                 deltaPSI = electrochemical potential
                        #                 deltaPH = pH gradient
                        #   for transport reactions involving the movement of reactant to product
                        #           of the form a*produc = b*react, the equation
                        #           can be broken into two parts: 1 for product and 1 for reactant  
                        #           each with 1/2 the contribution to the net reactions
                        #         dG_r_mem = dG_r_mem_prod + dG_r_mem_react
                        #           where dG_r_mem_prod = abs(a)/2*charge_prod/2*F(33.3*sign(a)*pH_comp_prod-143.33/2)
                        #                 dG_r_mem_react = abs(b)/2*charge_react/2*F(33.3*sign(b)*pH_comp_react-143.33/2)
                    
                        # calculate the contribution of charge transfer accross the membrane to dG_r
                        if p.name in mets_trans: dG_r_mem += fabs(r.get_coefficient(p.id))/2.0*p.charge/2.0*self.F*(33.3*r.get_coefficient(p.id)/fabs(r.get_coefficient(p.id))*pH[p.compartment]['pH']-143.33/2.0);

                        nMets = nMets + 1.0;
                        nMets_measured = nMets_measured + 1.0;

                        #dG_r_ionic = dG_r_ionic - log(10)*self.R*temperature[p.compartment]['temperature']*A*\
                        #                (p.charge*p.charge*r.get_coefficient(p.id)*ionic_strength[p.compartment]['ionic_strength'])/(1+B*ionic_strength[p.compartment]['ionic_strength']);
                        #dG_r_pH = dG_r_pH + log(10)*self.R*temperature[p.compartment]['temperature']*p.charge*pH[p.compartment]['pH'];

                    elif p.id in estimated_concentration.keys():
                        # calculate the dG_r of the reactants using estimated concentrations
                        #   NOTE: since the geometric mean is linear with respect to dG, no adjustments needs to be made
                        dG_r_product = dG_r_product + self.R*temperature[p.compartment]['temperature']*\
                                                            log(estimated_concentration[p.id]['concentration'])*r.get_coefficient(p.id);
                        # calculate the variance contributed to dG_r by the uncertainty in the measured concentrations
                        #   NOTE: provides an estimate only...
                        #         i.e. improvements need to be made
                        #dG_r_product_var = dG_r_product_var + self.R*temperature[p.compartment]['temperature']*fabs(r.get_coefficient(p.id))/estimated_concentration[p.id]['concentration']*\
                        #                                        estimated_concentration[p.id]['concentration_var'];
                        #dG_r_product_var = dG_r_product_var + exp(self.R*temperature[p.compartment]['temperature']*fabs(r.get_coefficient(p.id))/log(estimated_concentration[p.id]['concentration'])*\
                        #                                        log(estimated_concentration[p.id]['concentration_var']));

                        ## calculate the lb and ub for dG implementation #1
                        #dG_r_product_lb = dG_r_product_lb + self.R*temperature[p.compartment]['temperature']*\
                        #                                    log(estimated_concentration[p.id]['concentration_lb'])*r.get_coefficient(p.id);
                        #dG_r_product_ub = dG_r_product_ub + self.R*temperature[p.compartment]['temperature']*\
                        #                                    log(estimated_concentration[p.id]['concentration_ub'])*r.get_coefficient(p.id);
                        # calculate the lb and ub for dG implementation #2
                        dG_r_product_lb = dG_r_product_lb + self.R*temperature[p.compartment]['temperature']*\
                                                            log(estimated_concentration[p.id]['concentration_lb'])*r.get_coefficient(p.id);
                        dG_r_product_ub = dG_r_product_ub + self.R*temperature[p.compartment]['temperature']*\
                                                            log(estimated_concentration[p.id]['concentration_ub'])*r.get_coefficient(p.id);
                    
                        # calculate the contribution of charge transfer accross the membrane to dG_r
                        if p.name in mets_trans: dG_r_mem += fabs(r.get_coefficient(p.id))/2.0*p.charge/2.0*self.F*(33.3*r.get_coefficient(p.id)/fabs(r.get_coefficient(p.id))*pH[p.compartment]['pH']-143.33/2.0);

                        nMets = nMets + 1.0;
                        #dG_r_ionic = dG_r_ionic - log(10)*self.R*temperature[p.compartment]['temperature']*A*\
                        #                (p.charge*p.charge*r.get_coefficient(p.id)*ionic_strength[p.compartment]['ionic_strength'])/(1+B*ionic_strength[p.compartment]['ionic_strength']);
                        #dG_r_pH = dG_r_pH + log(10)*self.R*temperature[p.compartment]['temperature']*p.charge*pH[p.compartment]['pH'];

                    else:
                        # raise error
                        return
                else:
                    if p.name in mets_trans: 
                        dG_r_mem += fabs(r.get_coefficient(p.id))/2.0*p.charge/2.0*self.F*(33.3*r.get_coefficient(p.id)/fabs(r.get_coefficient(p.id))*pH[p.compartment]['pH']-143.33/2.0);
                        dG_r_pH = dG_r_pH - log(10)*self.R*temperature[p.compartment]['temperature']*p.charge*pH[p.compartment]['pH']*r.get_coefficient(p.id)/2.0;

                temperatures.append(temperature[p.compartment]['temperature']);
            # calculate dG_r for reactants
            dG_r_reactant = 0.0;
            dG_r_reactant_var = 0.0;
            dG_r_reactant_lb = 0.0;
            dG_r_reactant_ub = 0.0;
            for react in r.reactants:
                if not(react.id in hydrogens): # exclude hydrogen because it has already been accounted for when adjusting for the pH
                    if react.id in measured_concentration.keys():
                        # calculate the dG_r of the reactants using measured concentrations
                        #   NOTE: since the geometric mean is linear with respect to dG, no adjustments needs to be made
                        dG_r_reactant = dG_r_reactant + self.R*temperature[react.compartment]['temperature']*\
                                                            log(measured_concentration[react.id]['concentration'])*r.get_coefficient(react.id);
                        # calculate the variance contributed to dG_r by the uncertainty in the measured concentrations
                        #   NOTE: provides an estimate only...
                        #         i.e. improvements need to be made
                        #dG_r_reactant_var = dG_r_reactant_var + self.R*temperature[react.compartment]['temperature']*fabs(r.get_coefficient(react.id))/measured_concentration[react.id]['concentration']*measured_concentration[react.id]['concentration_var'];
                        #dG_r_reactant_var = dG_r_reactant_var + exp(self.R*temperature[react.compartment]['temperature']*fabs(r.get_coefficient(react.id))/log(measured_concentration[react.id]['concentration'])*log(measured_concentration[react.id]['concentration_var']));
                    
                        ## calculate the lb and ub for dG implementation #1
                        #dG_r_reactant_lb = dG_r_reactant_lb + self.R*temperature[react.compartment]['temperature']*\
                        #                                    log(measured_concentration[react.id]['concentration_lb'])*r.get_coefficient(react.id);
                        #dG_r_reactant_ub = dG_r_reactant_ub + self.R*temperature[react.compartment]['temperature']*\
                        #                                    log(measured_concentration[react.id]['concentration_ub'])*r.get_coefficient(react.id);
                        # calculate the lb and ub for dG implementation #2
                        dG_r_reactant_lb = dG_r_reactant_lb + self.R*temperature[react.compartment]['temperature']*\
                                                            log(measured_concentration[react.id]['concentration_ub'])*r.get_coefficient(react.id);
                        dG_r_reactant_ub = dG_r_reactant_ub + self.R*temperature[react.compartment]['temperature']*\
                                                            log(measured_concentration[react.id]['concentration_lb'])*r.get_coefficient(react.id);

                        # calculate the contribution of charge transfer accross the membrane to dG_r
                        #   NOTE: dG_r_mem = c*F*deltaPsi = c*F*(33.33*deltaPH-143.33)
                        #           where c = net charge transport
                        #                 F = Faradays constant
                        #                 deltaPSI = electrochemical potential
                        #                 deltaPH = pH gradient
                        #   for transport reactions involving the movement of reactant to product
                        #           of the form a*produc = b*react, the equation
                        #           can be broken into two parts: 1 for product and 1 for reactant  
                        #           each with 1/2 the contribution to the net reactions
                        #         dG_r_mem = dG_r_mem_prod + dG_r_mem_react
                        #           where dG_r_mem_prod = abs(a)/2*charge_prod/2*F(33.3*sign(a)*pH_comp_prod-143.33/2)
                        #                 dG_r_mem_react = abs(b)/2*charge_react/2*F(33.3*sign(b)*pH_comp_react-143.33/2)
                        if react.name in mets_trans: dG_r_mem += fabs(r.get_coefficient(react.id))/2.0*react.charge/2.0*self.F*(33.3*r.get_coefficient(react.id)/fabs(r.get_coefficient(react.id))*pH[react.compartment]['pH']-143.33/2.0);

                        nMets = nMets + 1.0;
                        nMets_measured = nMets_measured + 1.0;

                        #dG_r_ionic = dG_r_ionic - log(10)*self.R*temperature[react.compartment]['temperature']*A*\
                        #                (react.charge*react.charge*r.get_coefficient(react.id)*ionic_strength[react.compartment]['ionic_strength'])/(1+B*ionic_strength[react.compartment]['ionic_strength']);
                        #dG_r_pH = dG_r_pH + log(10)*self.R*temperature[react.compartment]['temperature']*react.charge*pH[react.compartment]['pH']*r.get_coefficient(react.id);

                    elif react.id in estimated_concentration.keys():
                        # calculate the dG_r of the reactants using estimated concentrations
                        #   NOTE: since the geometric mean is linear with respect to dG, no adjustments needs to be made
                        dG_r_reactant = dG_r_reactant + self.R*temperature[react.compartment]['temperature']*\
                                                            log(estimated_concentration[react.id]['concentration'])*r.get_coefficient(react.id);
                        # calculate the variance contributed to dG_r by the uncertainty in the measured concentrations
                        #   NOTE: provides an estimate only...
                        #         i.e. improvements need to be made
                        #dG_r_reactant_var = dG_r_reactant_var + self.R*temperature[react.compartment]['temperature']*fabs(r.get_coefficient(react.id))/estimated_concentration[react.id]['concentration']*\
                        #                                        estimated_concentration[react.id]['concentration_var'];
                        #dG_r_reactant_var = dG_r_reactant_var + exp(self.R*temperature[react.compartment]['temperature']*fabs(r.get_coefficient(react.id))/log(estimated_concentration[react.id]['concentration'])*\
                        #                                        log(estimated_concentration[react.id]['concentration_var']));
                    
                        ## calculate the lb and ub for dG implementation #1
                        #dG_r_reactant_lb = dG_r_reactant_lb + self.R*temperature[react.compartment]['temperature']*\
                        #                                    log(estimated_concentration[react.id]['concentration_lb'])*r.get_coefficient(react.id);
                        #dG_r_reactant_ub = dG_r_reactant_ub + self.R*temperature[react.compartment]['temperature']*\
                        #                                    log(estimated_concentration[react.id]['concentration_ub'])*r.get_coefficient(react.id);
                        # calculate the lb and ub for dG implementation #2
                        dG_r_reactant_lb = dG_r_reactant_lb + self.R*temperature[react.compartment]['temperature']*\
                                                            log(estimated_concentration[react.id]['concentration_ub'])*r.get_coefficient(react.id);
                        dG_r_reactant_ub = dG_r_reactant_ub + self.R*temperature[react.compartment]['temperature']*\
                                                            log(estimated_concentration[react.id]['concentration_lb'])*r.get_coefficient(react.id);
                    
                        # calculate the contribution of charge transfer accross the membrane to dG_r
                        if react.name in mets_trans: dG_r_mem += fabs(r.get_coefficient(react.id))/2.0*react.charge/2.0*self.F*(33.3*r.get_coefficient(react.id)/fabs(r.get_coefficient(react.id))*pH[react.compartment]['pH']-143.33/2.0);

                        nMets = nMets + 1.0;
                        #dG_r_ionic = dG_r_ionic - log(10)*self.R*temperature[react.compartment]['temperature']*A*\
                        #                (react.charge*react.charge*r.get_coefficient(react.id)*ionic_strength[react.compartment]['ionic_strength'])/(1+B*ionic_strength[react.compartment]['ionic_strength']);
                    else:
                        # raise error
                        return
                else: 
                    if react.name in mets_trans: 
                        dG_r_mem += fabs(r.get_coefficient(react.id))/2.0*react.charge/2.0*self.F*(33.3*r.get_coefficient(react.id)/fabs(r.get_coefficient(react.id))*pH[react.compartment]['pH']-143.33/2.0);
                        dG_r_pH = dG_r_pH + log(10)*self.R*temperature[react.compartment]['temperature']*pH[react.compartment]['pH']*r.get_coefficient(react.id)/2.0;
                temperatures.append(temperature[react.compartment]['temperature']);

            # adjustment for transport reactions:
            dG_r_trans = dG_r_mem + dG_r_pH;

            # calculate the dG_r for the reaction
            dG_r_I[r.id]['dG_r'] = self.dG0_r[r.id]['dG_r'] + dG_r_product + dG_r_reactant + dG_r_trans;
            dG_r_I[r.id]['dG_r_var'] = self.dG0_r[r.id]['dG_r_var'] + dG_r_product_var + dG_r_reactant_var;
            dG_r_I[r.id]['dG_r_lb'] = self.dG0_r[r.id]['dG_r_lb'] + dG_r_product_lb + dG_r_reactant_lb + dG_r_trans;
            dG_r_I[r.id]['dG_r_ub'] = self.dG0_r[r.id]['dG_r_ub'] + dG_r_product_ub + dG_r_reactant_ub + dG_r_trans;
            dG_r_I[r.id]['dG_r_units'] = 'kJ/mol';

            ## not the best way to calculate the "in vivo" Keq
            #Keq_exp = -self.dG0_r[r.id]['dG_r']/(sum(temperatures)/len(temperatures)*self.R);
            #if Keq_exp>100: Keq_exp = 100;
            #dG_r_I[r.id]['Keq'] = exp(Keq_exp);

            # determine the thermodynamic coverage
            if nMets == 0:  conc_coverage = 0;
            else: conc_coverage = nMets_measured/nMets
            metabolomics_coverage_I[r.id] = conc_coverage;

        self.dG_r = dG_r_I;
        self.metabolomics_coverage = metabolomics_coverage_I;

    def check_thermodynamicConsistency(self, cobra_model, reaction_bounds,
                           measured_concentration, estimated_concentration,
                           pH, ionic_strength, temperature, 
                           measured_concentration_coverage_criteria = 0.5,
                           measured_dG_f_coverage_criteria = 0.99):
        """identify thermodynamically infeasible reactions"""

        # determine if the reaction is thermodynamically infeasible using the lb and ub
        # In progress:
        #   calculate the reversibility index under pseudo-conditions
        #   calculate the reversibility index in vivo
    
        # initialize hydrogens:
        hydrogens = [];
        compartments = list(set(cobra_model.metabolites.list_attr('compartment')));
        for compart in compartments:
             hydrogens.append('h_' + compart);

        for r in cobra_model.reactions:
            self.thermodynamic_consistency_check[r.id] = None;

            ## In progress...
            #np = 0.0;
            #nr = 0.0;
            #Q_estimate = 0.0;
            #Q = 0.0;
            #for p in r.products:
            #    if not(p.id in hydrogens): # exclude hydrogen because it has already been accounted for when adjusting for the pH
            #        if p.id in measured_concentration.keys():
            #            Q += log(measured_concentration[p.id]['concentration'])*r.get_coefficient(p.id);
            #            Q_estimate += log(estimated_concentration[p.id]['concentration'])*r.get_coefficient(p.id);
            #            np += 1.0;
            #        elif p.id in estimated_concentration.keys():
            #            Q += log(estimated_concentration[p.id]['concentration'])*r.get_coefficient(p.id);
            #            Q_estimate += log(estimated_concentration[p.id]['concentration'])*r.get_coefficient(p.id);
            #            np += 1.0;
            #        else:
            #            # raise error
            #            return
        
            #for react in r.reactants:
            #    if not(react.id in hydrogens): # exclude hydrogen because it has already been accounted for when adjusting for the pH
            #        if react.id in measured_concentration.keys():
            #            Q += log(measured_concentration[react.id]['concentration'])*r.get_coefficient(react.id);
            #            Q_estimate += log(estimated_concentration[react.id]['concentration'])*r.get_coefficient(react.id);
            #            nr += 1.0;
            #        elif react.id in estimated_concentration.keys():
            #            Q += log(estimated_concentration[react.id]['concentration'])*r.get_coefficient(react.id);
            #            Q_estimate += log(estimated_concentration[react.id]['concentration'])*r.get_coefficient(react.id);
            #            nr += 1.0;
            #        else:
            #            # raise error
            #            return

            #Q = exp(Q);
            #Q_estimate = exp(Q_estimate);
            #self.dG_r[r.id]['Q'] = Q;
            #self.dG_r[r.id]['Q_estimate'] = Q_estimate;
            #if not (np+nr==0):
            #    self.dG_r[r.id]['ri'] = pow((self.dG_r[r.id]['Keq']/Q),(2.0/(np+nr)));
            #    self.dG_r[r.id]['ri_estimate'] = pow((self.dG_r[r.id]['Keq']/Q_estimate),(2.0/(np+nr)));

            feasible = False;
            if self.dG_r[r.id]['dG_r_ub']*reaction_bounds[r.id]['flux_lb']<=0 or\
               self.dG_r[r.id]['dG_r_ub']*reaction_bounds[r.id]['flux_ub']<=0 or\
               self.dG_r[r.id]['dG_r_lb']*reaction_bounds[r.id]['flux_lb']<=0 or\
               self.dG_r[r.id]['dG_r_lb']*reaction_bounds[r.id]['flux_ub']<=0:
                feasible = True;

            self.thermodynamic_consistency_check[r.id] = feasible;

        infeasible_reactions = [];
        for r in cobra_model.reactions:
            if not self.thermodynamic_consistency_check[r.id] and \
                self.metabolomics_coverage[r.id] > measured_concentration_coverage_criteria and \
                self.dG_r_coverage[r.id]>measured_dG_f_coverage_criteria:
                infeasible_reactions.append(r);

        '''Summarize the thermodynamic consistency check'''
        # analysis summary:
        print 'thermodynamically infeasible reactions identified:';
        for r in infeasible_reactions:
            print r.id, r.build_reaction_string();

        conc_coverage_cnt = 0;
        for k,v in self.metabolomics_coverage.iteritems():
            if v > measured_concentration_coverage_criteria:
                conc_coverage_cnt += 1;
                print k, v
        print ('total # of reactions with required metabolomic coverage = ' + str(conc_coverage_cnt))

        dG_f_coverage_cnt = 0;
        for k,v in self.dG_r_coverage.iteritems():
            if v > measured_dG_f_coverage_criteria:
                dG_f_coverage_cnt += 1;
                print k, v
        print ('total # of reactions with required thermodynamic coverage = ' + str(dG_f_coverage_cnt))

    def export_summary(self,cobra_model,reaction_bounds,filename):
        '''Summarize the results of the thermodynamics analysis'''
        # make header
        header = ["reaction_id","reaction_formula","dG0_r","dG_r_lb","dG_r_ub","flux_lb","flux_ub","metabolomics_coverage","dG_coverage","is_feasible"];
        # make rows
        rows = []; 
        for rxn in self.dG_r:
            row = [];
            row = [rxn,cobra_model.reactions.get_by_id(rxn).build_reaction_string(),\
                self.dG0_r[rxn]['dG_r'],self.dG_r[rxn]['dG_r_lb'],self.dG_r[rxn]['dG_r_ub'],\
                reaction_bounds[rxn]['flux_lb'],reaction_bounds[rxn]['flux_ub'],\
                self.metabolomics_coverage[rxn],self.dG_r_coverage[rxn],self.thermodynamic_consistency_check[rxn]];
            rows.append(row);

            #print "reaction_id\treaction_formula\tdG0_r\tdG_r_lb\tdG_r_ub\tflux_lb\tflux_ub\tmetabolomics_coverage\tdG_coverage\tis_feasible"
            #print rxn,\
            #    cobra_model.reactions.get_by_id(rxn).build_reaction_string(),\
            #    self.dG0_r[rxn]['dG_r'],self.dG_r[rxn]['dG_r_lb'],self.dG_r[rxn]['dG_r_ub'],\
            #    reaction_bounds[rxn]['flux_lb'],reaction_bounds[rxn]['flux_ub'],\
            #    self.metabolomics_coverage[rxn],self.dG_r_coverage[rxn],self.thermodynamic_consistency_check[rxn];

        with open(filename, 'wb') as f:
            writer = csv.writer(f);
            try:
                writer.writerow(header);
                writer.writerows(rows);
            except csv.Error as e:
                sys.exit(e);
    
    def find_transportMets(self, cobra_model_I, reaction_id_I):
        # transport metabolite definition:
        #	1. different id (same base id but different compartment)
        #	2. same name
        #	3. different compartment

        ''' Not full proof
        import re
        compartments = list(set(cobra_model.metabolites.list_attr('compartment')));
        met_ID
        for compart in compartments:
	        comp_tmp = '_' + compart;
	        met_tmp = re.sub(comp_tmp,'',met_ID)
        met_ids.append(met_tmp)'''

        met_ids = cobra_model_I.reactions.get_by_id(reaction_id_I).products + cobra_model_I.reactions.get_by_id(reaction_id_I).reactants
        met_names = [];
        mets_O = [];
        for m in met_ids:
            met_names.append(m.name);
        met_O = [k for k,v in Counter(met_names).items() if v>1]
        return met_O;

