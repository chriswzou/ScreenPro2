## Copyright (c) 2022-2024 ScreenPro2 Development Team.
## All rights reserved.
## Gilbart Lab, UCSF / Arc Institute.
## Multi-Omics Tech Center, Arc Insititue.

"""phenoscore module

This module contains functions for calculating relative phenotypes from CRISPR screens
datasets.
"""

import numpy as np
import anndata as ad
import pandas as pd

from .delta import calculateDelta, generatePseudoGeneAnnData, averageBestN
from .phenostat import matrixStat
from .deseq import runDESeq, extractDESeqResults
from .annotate import annotateScoreTable
from .phenostat import multipleTestsCorrection


def runPhenoScore(adata, cond_ref, cond_test, score_level, test,
                  growth_rate=1, n_reps='auto', keep_top_n = None,
                  num_pseudogenes='auto', pseudogene_size='auto',
                  count_layer=None, ctrl_label='negative_control'):
    """Calculate phenotype score and p-values when comparing `cond_test` vs `cond_ref`.

    Args:
        adata (AnnData): AnnData object
        cond_ref (str): condition reference
        cond_test (str): condition test
        score_level (str): score level
        test (str): test to use for calculating p-value ('MW': Mann-Whitney U rank; 'ttest' : t-test)
        growth_rate (int): growth rate
        n_reps (int): number of replicates
        keep_top_n (int): number of top guides to keep per target
        num_pseudogenes (int): number of pseudogenes to generate
        pseudogene_size (int): number of sgRNA elements in each pseudogene
        count_layer (str): count layer to use for calculating score, default is None (use default count layer in adata.X)
        ctrl_label (str): control label, default is 'negative_control'
    
    Returns:
        str: result name
        pd.DataFrame: result dataframe
    """
    adat = adata.copy()

    # format result name
    result_name = f'{cond_test}_vs_{cond_ref}'
    print(f'\t{cond_test} vs {cond_ref}')

    # set n_reps if not provided
    if n_reps == 'auto':
        n_reps = min(
            adat.obs.query(f'condition=="{cond_ref}"').shape[0], 
            adat.obs.query(f'condition=="{cond_test}"').shape[0]
        )

    # check if count_layer exists
    if count_layer is None:
        pass
    elif count_layer not in adat.layers.keys():
        raise ValueError(f"Layer '{count_layer}' not found in adata.layers.keys().")
    elif count_layer in adat.layers.keys():
        adat.X = adat.layers[count_layer].copy()
    
    # evaluate library table to get targets and riase error if not present
    required_columns = ['target'] #, 'sequence']
    missing_columns = list(set(required_columns) - set(adat.var.columns))
    if len(missing_columns) > 0:
        raise ValueError(f"Missing required columns in library table: {missing_columns}")

    # calc phenotype score and p-value
    if score_level in ['compare_reps']:
        # prep counts for phenoScore calculation
        df_cond_ref = adat[adat.obs.query(f'condition=="{cond_ref}"').index[:n_reps],].to_df(count_layer).T
        df_cond_test = adat[adat.obs.query(f'condition=="{cond_test}"').index[:n_reps],].to_df(count_layer).T

        # convert to numpy arrays
        x = df_cond_ref.to_numpy()
        y = df_cond_test.to_numpy()

        # get control values
        x_ctrl = df_cond_ref[adat.var.targetType.eq(ctrl_label)].to_numpy()
        y_ctrl = df_cond_test[adat.var.targetType.eq(ctrl_label)].to_numpy()

        # calculate phenotype scores
        scores = calculateDelta(
            x = x, y = y, 
            x_ctrl = x_ctrl, y_ctrl = y_ctrl, 
            growth_rate = growth_rate,
        )

        # average scores across replicates
        scores = [np.mean(s) for s in scores]
        
        # compute p-value
        p_values = matrixStat(x, y, test=test, level = 'col')

        # get adjusted p-values
        adj_p_values = multipleTestsCorrection(p_values)
                
        # get targets
        targets = adat.var['target'].to_list()

        # combine results into a dataframe
        result = pd.concat([
            pd.Series(targets, index=adat.var.index, name='target'),
            pd.Series(scores, index=adat.var.index, name='score'),
        ], axis=1)
        
        # add p-values
        result[f'{test} pvalue'] = p_values
        result['BH adj_pvalue'] = adj_p_values
    
    elif score_level in ['compare_guides']:

        # prep counts for phenoScore calculation
        df_cond_ref = adat[adat.obs.query(f'condition=="{cond_ref}"').index].to_df().T
        df_cond_test = adat[adat.obs.query(f'condition=="{cond_test}"').index].to_df().T
        # get control values
        x_ctrl = df_cond_ref[adat.var.targetType.eq(ctrl_label)].to_numpy()
        y_ctrl = df_cond_test[adat.var.targetType.eq(ctrl_label)].to_numpy()
        del df_cond_ref, df_cond_test
        
        adat_pseudo = generatePseudoGeneAnnData(adat, num_pseudogenes=num_pseudogenes, pseudogene_size=pseudogene_size, ctrl_label=ctrl_label)
        
        adat_test = ad.concat([adat[:,~adat.var.targetType.eq(ctrl_label)], adat_pseudo], axis=1)
        adat_test.obs = adat.obs.copy()

        # prep counts for phenoScore calculation
        df_cond_ref = adat_test[adat_test.obs.query(f'condition=="{cond_ref}"').index].to_df().T
        df_cond_test = adat_test[adat_test.obs.query(f'condition=="{cond_test}"').index].to_df().T
        
        targets = []
        scores = []
        p_values = []
        
        # group by target genes or pseudogenes to aggregate counts for score calculation
        for target_name, target_group in adat_test.var.groupby('target'):
            # select target group and convert to numpy arrays
            x = df_cond_ref.loc[target_group.index,:].to_numpy()
            y = df_cond_test.loc[target_group.index,:].to_numpy()

            # calculate phenotype scores
            target_scores = calculateDelta(
                x = x, y = y, 
                x_ctrl = x_ctrl, y_ctrl = y_ctrl, 
                growth_rate = growth_rate,
            )
            
            if keep_top_n is None or keep_top_n is False:
                # average scores across guides
                target_scores = np.mean(target_scores, axis=0)

            elif keep_top_n > 0:
                # get top n scores per target
                np.apply_along_axis(averageBestN, axis=0, arr=target_scores, numToAverage=keep_top_n)
            
            else:
                raise ValueError(f'Invalid value for keep_top_n: {keep_top_n}')

            # compute p-value
            target_p_values = matrixStat(x, y, test=test, level='all')
            
            scores.append(target_scores)
            p_values.append(target_p_values)
            targets.append(target_name)

        # average scores across replicates
        scores = [np.mean(s) for s in scores]

        # get adjusted p-values
        adj_p_values = multipleTestsCorrection(np.array(p_values))
        
        # combine results into a dataframe
        result = pd.concat([
            pd.Series(targets, index=targets, name='target'),
            pd.Series(scores, index=targets, name='score'),
            pd.Series(p_values, index=targets, name=f'{test} pvalue'),
            pd.Series(adj_p_values, index=targets, name='BH adj_pvalue'),
        ], axis=1)

        # rename pseudo genes in target column to `ctrl_label`
        result['target'] = result['target'].apply(lambda x: ctrl_label if 'pseudo' in x else x)

    
    else:
        raise ValueError(f'score_level "{score_level}" not recognized. Currently, "compare_reps" and "compare_guides" are supported.')
    
    return result_name, result


def runPhenoScoreForReplicate(adata, x_label, y_label, growth_rate_reps=None, ctrl_label='negative_control'):
    """Calculate phenotype score for each pair of replicates.

    Args:
        adata (AnnData): AnnData object
        x_label: name of the first condition in column `condition` of `screen.adata.obs`
        y_label: name of the second condition in column `condition` of `screen.adata.obs`
        growth_rate_reps (dict): dictionary of growth rates for each replicate
        ctrl_label: string to identify labels of negative control elements in sgRNA library (default is 'negative_control')

    Returns:
        pd.DataFrame: dataframe of phenotype scores
    """
    adat = adata.copy()

    adat_ctrl = adat[:, adat.var.targetType.eq(ctrl_label)].copy()

    results = {}

    if growth_rate_reps is None:
        growth_rate_reps = dict([(replicate, 1) for replicate in adat.obs.replicate.unique()])

    for replicate in adat.obs.replicate.unique():

        res = calculatePhenotypeScore(
            x=adat[adat.obs.query(f'condition == "{x_label}" & replicate == {str(replicate)}').index].X,
            y=adat[adat.obs.query(f'condition == "{y_label}" & replicate == {str(replicate)}').index].X,
            x_ctrl=adat_ctrl[adat_ctrl.obs.query(f'condition == "{x_label}" & replicate == {str(replicate)}').index].X,
            y_ctrl=adat_ctrl[adat_ctrl.obs.query(f'condition == "{y_label}" & replicate == {str(replicate)}').index].X,
            growth_rate=growth_rate_reps[replicate],
            level='row'  # there is only one column so `row` option here is equivalent to the value before averaging.
        )

        results.update({f'replicate_{replicate}': res.reshape(-1)})

    out = pd.DataFrame(
        results,
        index=adat.var.index
    )

    return out
