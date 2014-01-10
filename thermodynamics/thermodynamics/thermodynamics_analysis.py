from __future__ import with_statement
#cobra.flux_analysis.variablity.py
#runs flux variablity analysis on a Model object.
from math import floor,ceil,log,sqrt,pow,exp,fabs
from copy import deepcopy
from cobra.core.Metabolite import Metabolite
from cobra.core.Reaction import Reaction

def thermodynamic_analysis(cobra_model, reaction_bounds, 
                           measured_concentration, measured_dG_f,
                           estimated_concentration, estimated_dG_f,
                           pH, ionic_strength, temperature = 298.15, 
                           measured_concentration_coverage_criteria = 0.5,
                           measured_dG_f_coverage_criteria = 0.99):

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
    # check units
    # check compartments
    # check for metabolite redundancy

    # initialize constants
    R = 8.314e-3; # gas constant 8.3144621 [kJ/K/mol]
    # T = 298.15; # temperature [K]
    A = 0.5093; # parameter A of the extended Debye-Huckel equation
                # [mol^.5*L^-.5]
    B = 1.6; # parameter B of the extended Debye-Huckel equation
             # [mol^.5*L^-.5]
    F = 96.485; # faraday's constant [kJ/V/mol]
    Rkcal = 1.9858775e-3; # gas constant 8.3144621 [kcal/K/mol]
    Fkcal = 23.062e-3; # faraday's constant [kcal/mV/mol]

    # initialize hydrogens:
    hydrogens = [];
    compartments = list(set(cobra_model.metabolites.list_attr('compartment')));
    for compart in compartments:
         hydrogens.append('h_' + compart);

    # initialize return variables
    dG_r = {};
    thermodynamic_consistency_check = {};

    """calculate the standard Gibbs free energy of reaction"""

    """
    a quick primer on the propogation of uncertainty:
    Thanks to Glen Tesler
    http://books.google.com/books?id=csTsQr-v0d0C&pg=PA56

    X = measured value
    x = actual value
    ex = error in x
    X = x + ex

    y = f(x) transformation function
    Y = experimental transformed value
    Y = f(X) = f(x+ex) = y + fprime(x)*ex + o(ex)
    ex = fprime(x)*ex + o(ex)
    
    first order approximation
    sigma2y = variance in the transformed measured value
    sigma2x = variance in the measured value
    E = expected value
    
    sigma2y = E*(ex)^2 = (fprime(x))^2*E*(ex)^2
    
    for dGr = R*T*ln(K)
    sigma2y = (R*T/K)^2*sigma2x
    """

    dG0_r = {};
    for r in cobra_model.reactions:
        dG0_r[r.id] = {'dG_r': None,
                       'dG_r_var': None,
                       'dG_r_lb': None,
                       'dG_r_ub': None,
                       'dG_r_units': None};
        thermodynamic_consistency_check[r.id] = {'feasible': None,
                                                 'measured_concentration_coverage': None,
                                                 'measured_dG_r_coverage': None}; # will determine the coverage of thermodynamic
        
        nMets = 0.0; # number of metabolites in each reaction
        nMets_measured = 0.0; # number of measured metabolites in each reaction
        # calculate dG0_r for products
        dG0_r_product = 0.0;
        dG0_r_product_var = 0.0;
        dG0_r_product_lb = 0.0;
        dG0_r_product_ub = 0.0;
        for p in r.get_products():
            if p.id in measured_dG_f.keys():
                dG0_r_product = dG0_r_product + measured_dG_f[p.id]['dG_f']*r.get_coefficient(p.id)
                dG0_r_product_var = dG0_r_product_var + measured_dG_f[p.id]['dG_f_var']
                dG0_r_product_lb = dG0_r_product_lb + measured_dG_f[p.id]['dG_f_lb']*r.get_coefficient(p.id)
                dG0_r_product_ub = dG0_r_product_ub + measured_dG_f[p.id]['dG_f_ub']*r.get_coefficient(p.id)
                nMets = nMets + 1.0;
                nMets_measured = nMets_measured + 1.0;
            elif p.id in estimated_dG_f.keys():
                dG0_r_product = dG0_r_product + estimated_dG_f[p.id]['dG_f']*r.get_coefficient(p.id)
                dG0_r_product_var = dG0_r_product_var + estimated_dG_f[p.id]['dG_f_var']
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
        for react in r.get_reactants():
            if react.id in measured_dG_f.keys():
                dG0_r_reactant = dG0_r_reactant + measured_dG_f[react.id]['dG_f']*r.get_coefficient(react.id)
                dG0_r_reactant_var = dG0_r_reactant_var + measured_dG_f[react.id]['dG_f_var']
                dG0_r_reactant_lb = dG0_r_reactant_lb + measured_dG_f[react.id]['dG_f_lb']*r.get_coefficient(react.id)
                dG0_r_reactant_ub = dG0_r_reactant_ub + measured_dG_f[react.id]['dG_f_ub']*r.get_coefficient(react.id)
                nMets = nMets + 1.0;
                nMets_measured = nMets_measured + 1.0;
            elif react.id in estimated_dG_f.keys():
                dG0_r_reactant = dG0_r_reactant + estimated_dG_f[react.id]['dG_f']*r.get_coefficient(react.id)
                dG0_r_reactant_var = dG0_r_reactant_var + estimated_dG_f[react.id]['dG_f_var']
                dG0_r_reactant_lb = dG0_r_reactant_lb + estimated_dG_f[react.id]['dG_f_lb']*r.get_coefficient(react.id)
                dG0_r_reactant_ub = dG0_r_reactant_ub + estimated_dG_f[react.id]['dG_f_ub']*r.get_coefficient(react.id)
                nMets = nMets + 1.0;
            else:
                # raise error
                return
        # calculate the dG0_r for the reaction
        dG0_r[r.id]['dG_r'] = dG0_r_product + dG0_r_reactant;
        dG0_r[r.id]['dG_r_var'] = dG0_r_product_var + dG0_r_reactant_var;
        dG0_r[r.id]['dG_r_lb'] = dG0_r_product_lb + dG0_r_reactant_lb;
        dG0_r[r.id]['dG_r_ub'] = dG0_r_product_ub + dG0_r_reactant_ub;
        dG0_r[r.id]['dG_r_units'] = 'kJ/mol';

        # determine the thermodynamic coverage
        if nMets == 0:  dG_r_coverage = 0;
        else: dG_r_coverage = nMets_measured/nMets
        thermodynamic_consistency_check[r.id]['measured_dG_r_coverage'] = dG_r_coverage;

    """calculate the Gibbs free energy of reaction accounting for the following:
    metabolite concentrations
    pH (proton concentration)
    temperature
    ionic_strength
    transport processes (membrane potential and  proton exchange)"""
    dG_r = {};
    for r in cobra_model.reactions:
        dG_r[r.id] = {'dG_r': None,
                       'dG_r_var': None,
                       'dG_r_units': None,
                       'dG_r_lb': None,
                       'dG_r_ub': None,
                       'Keq': None,
                       'ri_estimate': None,
                       'ri': None,
                       'Q_estimate': None,
                       'Q': None};
        nMets = 0.0; # number of metabolites in each reaction
        nMets_measured = 0.0; # number of measured metabolites in each reaction

        temperatures = [];

        dG_r_I = 0.0; # adjustment for ionic strength (Henry et al, 2007, Biophysical Journal 92(5) 1792?1805)
        dG_r_pH = 0.0; # adjustment for pH (Robert A. Alberty, Thermodynamics of biochemical reactions (Hoboken N.J.: Wiley-Interscience, 2003).

        # adjustment for transport reactions (Henry et al, 2007, Biophysical Journal 92(5) 1792?1805)
        d_h = 0; # change in proton transfer accross the membrane
        compartments_p = {}; # compartments of the products {metabolite.compartment:{metabolite.id:metabolite.charge}}
        compartments_r = {}; # compartments of the reactants {metabolite.compartment:{metabolite.id:metabolite.charge}}
        dG_mem = 0.0; # change in membrane potential
        dG_pH = 0.0; # change in membrane pH

        # calculate dG_r for products
        dG_r_product = 0.0;
        dG_r_product_var = 0.0;
        dG_r_product_lb = 0.0;
        dG_r_product_ub = 0.0;
        for p in r.get_products():
            if not(p.id in hydrogens): # exclude hydrogen because it has already been accounted for when adjusting for the pH
                if p.id in measured_concentration.keys():
                    dG_r_product = dG_r_product + R*temperature[p.compartment]['temperature']*\
                                                        log(measured_concentration[p.id]['concentration'])*r.get_coefficient(p.id);
                    #dG_r_product_var = dG_r_product_var + pow(R*temperature[p.compartment]['temperature']*fabs(r.get_coefficient(p.id)),2)/measured_concentration[p.id]['concentration']*measured_concentration[p.id]['concentration_var'];
                    dG_r_product_var = dG_r_product_var + exp(pow(R*temperature[p.compartment]['temperature']*fabs(r.get_coefficient(p.id)),2)/log(measured_concentration[p.id]['concentration'])*log(measured_concentration[p.id]['concentration_var']));
                    
                    dG_r_product_lb = dG_r_product_lb + R*temperature[p.compartment]['temperature']*\
                                                        log(measured_concentration[p.id]['concentration_lb'])*r.get_coefficient(p.id);
                    dG_r_product_ub = dG_r_product_ub + R*temperature[react.compartment]['temperature']*\
                                                        log(measured_concentration[p.id]['concentration_ub'])*r.get_coefficient(p.id);
                    #dG_r_I = dG_r_I - log(10)*R*temperature[p.compartment]['temperature']*A*\
                    #                (p.charge*p.charge*r.get_coefficient(p.id)*ionic_strength[p.compartment]['ionic_strength'])/(1+B*ionic_strength[p.compartment]['ionic_strength']);
                    #dG_r_pH = dG_r_pH + log(10)*R*temperature[p.compartment]['temperature']*p.charge*pH[p.compartment]['pH'];
                    #compartments_p[p.compartment] = {p.id:p.charge};
                    #if ('h_' in p.id): d_h = d_h + 1;

                    nMets = nMets + 1.0;
                    nMets_measured = nMets_measured + 1.0;

                    temperatures.append(temperature[p.compartment]['temperature']);
                elif p.id in estimated_concentration.keys():
                    dG_r_product = dG_r_product + R*temperature[p.compartment]['temperature']*\
                                                        log(estimated_concentration[p.id]['concentration'])*r.get_coefficient(p.id);
                    #dG_r_product_var = dG_r_product_var + R*temperature[p.compartment]['temperature']*fabs(r.get_coefficient(p.id))/estimated_concentration[p.id]['concentration']*\
                    #                                        estimated_concentration[p.id]['concentration_var'];
                    dG_r_product_var = dG_r_product_var + exp(R*temperature[p.compartment]['temperature']*fabs(r.get_coefficient(p.id))/log(estimated_concentration[p.id]['concentration'])*\
                                                            log(estimated_concentration[p.id]['concentration_var']));
                    
                    dG_r_product_lb = dG_r_product_lb + R*temperature[p.compartment]['temperature']*\
                                                        log(estimated_concentration[p.id]['concentration_lb'])*r.get_coefficient(p.id);
                    dG_r_product_ub = dG_r_product_ub + R*temperature[p.compartment]['temperature']*\
                                                        log(estimated_concentration[p.id]['concentration_ub'])*r.get_coefficient(p.id);
                    #dG_r_I = dG_r_I - log(10)*R*temperature[p.compartment]['temperature']*A*\
                    #                (p.charge*p.charge*r.get_coefficient(p.id)*ionic_strength[p.compartment]['ionic_strength'])/(1+B*ionic_strength[p.compartment]['ionic_strength']);
                    #dG_r_pH = dG_r_pH + log(10)*R*temperature[p.compartment]['temperature']*p.charge*pH[p.compartment]['pH'];
                    #compartments_p[p.compartment] = {p.id:p.charge};
                    #if ('h_' in p.id): d_h = d_h + 1;

                    nMets = nMets + 1.0;

                    temperatures.append(temperature[p.compartment]['temperature']);
                else:
                    # raise error
                    return
            
            temperatures.append(temperature[p.compartment]['temperature']);
        # calculate dG_r for reactants
        dG_r_reactant = 0.0;
        dG_r_reactant_var = 0.0;
        dG_r_reactant_lb = 0.0;
        dG_r_reactant_ub = 0.0;
        for react in r.get_reactants():
            if not(react.id in hydrogens): # exclude hydrogen because it has already been accounted for when adjusting for the pH
                if react.id in measured_concentration.keys():
                    dG_r_reactant = dG_r_reactant + R*temperature[react.compartment]['temperature']*\
                                                        log(measured_concentration[react.id]['concentration'])*r.get_coefficient(react.id);
                    #dG_r_reactant_var = dG_r_reactant_var + R*temperature[react.compartment]['temperature']*fabs(r.get_coefficient(react.id))/measured_concentration[react.id]['concentration']*measured_concentration[react.id]['concentration_var'];
                    dG_r_reactant_var = dG_r_reactant_var + exp(R*temperature[react.compartment]['temperature']*fabs(r.get_coefficient(react.id))/log(measured_concentration[react.id]['concentration'])*log(measured_concentration[react.id]['concentration_var']));
                    
                    dG_r_reactant_lb = dG_r_reactant_lb + R*temperature[react.compartment]['temperature']*\
                                                        log(measured_concentration[react.id]['concentration_lb'])*r.get_coefficient(react.id);
                    dG_r_reactant_ub = dG_r_reactant_ub + R*temperature[react.compartment]['temperature']*\
                                                        log(measured_concentration[react.id]['concentration_ub'])*r.get_coefficient(react.id);

                    #dG_r_I = dG_r_I - log(10)*R*temperature[react.compartment]['temperature']*A*\
                    #                (react.charge*react.charge*r.get_coefficient(react.id)*ionic_strength[react.compartment]['ionic_strength'])/(1+B*ionic_strength[react.compartment]['ionic_strength']);
                    #dG_r_pH = dG_r_pH + log(10)*R*temperature[react.compartment]['temperature']*react.charge*pH[react.compartment]['pH'];
                    #compartments_p[react.compartment] = {react.id:react.charge};
                    #if ('h_' in react.id): d_h = d_h + 1;

                    nMets = nMets + 1.0;
                    nMets_measured = nMets_measured + 1.0;

                elif react.id in estimated_concentration.keys():
                    dG_r_reactant = dG_r_reactant + R*temperature[react.compartment]['temperature']*\
                                                        log(estimated_concentration[react.id]['concentration'])*r.get_coefficient(react.id);
                    #dG_r_reactant_var = dG_r_reactant_var + R*temperature[react.compartment]['temperature']*fabs(r.get_coefficient(react.id))/estimated_concentration[react.id]['concentration']*\
                    #                                        estimated_concentration[react.id]['concentration_var'];
                    dG_r_reactant_var = dG_r_reactant_var + exp(R*temperature[react.compartment]['temperature']*fabs(r.get_coefficient(react.id))/log(estimated_concentration[react.id]['concentration'])*\
                                                            log(estimated_concentration[react.id]['concentration_var']));
                    
                    dG_r_reactant_lb = dG_r_reactant_lb + R*temperature[react.compartment]['temperature']*\
                                                        log(estimated_concentration[react.id]['concentration_lb'])*r.get_coefficient(react.id);
                    dG_r_reactant_ub = dG_r_reactant_ub + R*temperature[react.compartment]['temperature']*\
                                                        log(estimated_concentration[react.id]['concentration_ub'])*r.get_coefficient(react.id);

                    #dG_r_I = dG_r_I - log(10)*R*temperature[react.compartment]['temperature']*A*\
                    #                (react.charge*react.charge*r.get_coefficient(react.id)*ionic_strength[react.compartment]['ionic_strength'])/(1+B*ionic_strength[react.compartment]['ionic_strength']);
                    #dG_r_pH = dG_r_pH + log(10)*R*temperature[react.compartment]['temperature']*react.charge*pH[react.compartment]['pH'];
                    #compartments_p[react.compartment] = {react.id:react.charge};
                    #if ('h_' in p.id): d_h = d_h + 1;

                    nMets = nMets + 1.0;

                else:
                    # raise error
                    return
            
            temperatures.append(temperature[react.compartment]['temperature']);
        # check for transport reactions:

        # calculate the dG_r for the reaction
        dG_r[r.id]['dG_r'] = dG0_r[r.id]['dG_r'] + dG_r_product + dG_r_reactant;
        dG_r[r.id]['dG_r_var'] = dG0_r[r.id]['dG_r_var'] + dG_r_product_var + dG_r_reactant_var;
        dG_r[r.id]['dG_r_lb'] = dG0_r[r.id]['dG_r_lb'] + dG_r_product_lb + dG_r_reactant_lb;
        dG_r[r.id]['dG_r_ub'] = dG0_r[r.id]['dG_r_ub'] + dG_r_product_ub + dG_r_reactant_ub;
        dG_r[r.id]['dG_r_units'] = 'kJ/mol';
        Keq_exp = -dG0_r[r.id]['dG_r']/(sum(temperatures)/len(temperatures)*R);
        if Keq_exp>100: Keq_exp = 100;
        dG_r[r.id]['Keq'] = exp(Keq_exp);
        # determine the thermodynamic coverage
        if nMets == 0:  conc_coverage = 0;
        else: conc_coverage = nMets_measured/nMets
        thermodynamic_consistency_check[r.id]['measured_concentration_coverage'] = conc_coverage;

    """identify thermodynamically infeasible reactions"""
    # calculate the reversibility index under pseudo-conditions
    # calculate the reversibility index in vivo
    # determine if the reaction is thermodynamically infeasible using the lb and ub

    for r in cobra_model.reactions:
        np = 0.0;
        nr = 0.0;
        Q_estimate = 0.0;
        Q = 0.0;
        for p in r.get_products():
            if not(p.id in hydrogens): # exclude hydrogen because it has already been accounted for when adjusting for the pH
                if p.id in measured_concentration.keys():
                    Q += log(measured_concentration[p.id]['concentration'])*r.get_coefficient(p.id);
                    Q_estimate += log(estimated_concentration[p.id]['concentration'])*r.get_coefficient(p.id);
                    np += 1.0;
                elif p.id in estimated_concentration.keys():
                    Q += log(estimated_concentration[p.id]['concentration'])*r.get_coefficient(p.id);
                    Q_estimate += log(estimated_concentration[p.id]['concentration'])*r.get_coefficient(p.id);
                    np += 1.0;
                else:
                    # raise error
                    return
        
        for react in r.get_reactants():
            if not(react.id in hydrogens): # exclude hydrogen because it has already been accounted for when adjusting for the pH
                if react.id in measured_concentration.keys():
                    Q += log(measured_concentration[react.id]['concentration'])*r.get_coefficient(react.id);
                    Q_estimate += log(estimated_concentration[react.id]['concentration'])*r.get_coefficient(react.id);
                    nr += 1.0;
                elif react.id in estimated_concentration.keys():
                    Q += log(estimated_concentration[react.id]['concentration'])*r.get_coefficient(react.id);
                    Q_estimate += log(estimated_concentration[react.id]['concentration'])*r.get_coefficient(react.id);
                    nr += 1.0;
                else:
                    # raise error
                    return

        Q = exp(Q);
        Q_estimate = exp(Q_estimate);
        dG_r[r.id]['Q'] = Q;
        dG_r[r.id]['Q_estimate'] = Q_estimate;
        if not (np+nr==0):
            dG_r[r.id]['ri'] = pow((dG_r[r.id]['Keq']/Q),(2.0/(np+nr)));
            dG_r[r.id]['ri_estimate'] = pow((dG_r[r.id]['Keq']/Q_estimate),(2.0/(np+nr)));

        feasible = False;
        if dG_r[r.id]['dG_r_ub']*reaction_bounds[r.id]['flux_lb']<=0 or\
           dG_r[r.id]['dG_r_ub']*reaction_bounds[r.id]['flux_ub']<=0 or\
           dG_r[r.id]['dG_r_lb']*reaction_bounds[r.id]['flux_lb']<=0 or\
           dG_r[r.id]['dG_r_lb']*reaction_bounds[r.id]['flux_ub']<=0:
            feasible = True;

        thermodynamic_consistency_check[r.id]['feasible'] = feasible;

    infeasible_reactions = [];
    for r in cobra_model.reactions:
        if not thermodynamic_consistency_check[r.id]['feasible'] and \
            thermodynamic_consistency_check[r.id]['measured_concentration_coverage'] > measured_concentration_coverage_criteria and \
            thermodynamic_consistency_check[r.id]['measured_dG_r_coverage']>measured_dG_f_coverage_criteria:
            infeasible_reactions.append(r);

    print 'thermodynamically infeasible reactions identified:';
    for r in infeasible_reactions:
        print r.id, r.build_reaction_string();

    return;
