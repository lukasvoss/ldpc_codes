from typing import List, Dict, Optional

import sys
import io
import numpy as np
import pickle
from itertools import product
import copy

from mip import Model, xsum, minimize, BINARY
from bposd.css import css_code

### Surpress the print output of the css_code.test() ###
original_stdout = sys.stdout

import logging
logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s INFO %(message)s",  # hardcoded INFO level
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)


def save_code_configs(
        my_codes: List[Dict], 
        file_name: str
    ):
    with open(file_name, 'wb') as f:
        pickle.dump(my_codes, f)

def get_net_encoding_rate(
        k: int,
        n: int,
) -> float:
    return k / (2.*n)

def search_codes_general(
        l_range: range, 
        m_range: range, 
        weight_range: range, 
        power_range_A: range, 
        power_range_B: range, 
        encoding_rate_threshold: Optional[float],
    ):
    """
    Searching the parameter space for good bicycle codes (BC)

    args:
        - l_range: Range of possible values for parameter l
        - m_range: Range of possible values for parameter m
        - weight_range: Range of code weights (= the total number of summands accumulated for both A and B)
        - power_range_A: Range of possible values for exponents for terms in A (A is a sum over polynomials in x and y)
        - power_range_B: Range of possible values for exponents for terms in B (B is a sum over polynomials in x and y)
        - encoding_rate_threshold (float): the lower bound for codes to be saved for further analysis
    """
    good_configs = []

    

    for l, m in product(l_range, m_range):
        try:
            I_ell = np.identity(l, dtype=int)
            I_m = np.identity(m, dtype=int)
            x, y = {}, {}

            # Generate base matrices x and y
            for i in range(l):
                x[i] = np.kron(np.roll(I_ell, i, axis=1), I_m)
            for j in range(m):
                y[j] = np.kron(I_ell, np.roll(I_m, j, axis=1))

            # Iterate over weights and distribute them across A and B
            for weight in weight_range:
                for weight_A in range(1, weight):  # Ensure at least one term in A and B # TODO: Could think of also raising to the power of zero leading to identity matrix
                    weight_B = weight - weight_A

                    # Generate all combinations of summands in A and B with their respective weights
                    summands_A = list(product(['x', 'y'], repeat=weight_A))
                    summands_B = list(product(['x', 'y'], repeat=weight_B))

                    for summand_combo_A, summand_combo_B in product(summands_A, summands_B):
                        # Iterate over power ranges for each summand in A and B
                        for powers_A in product(power_range_A, repeat=weight_A):
                            for powers_B in product(power_range_B, repeat=weight_B):
                                A, B = np.zeros((l*m, l*m), dtype=int), np.zeros((l*m, l*m), dtype=int)
                                A_poly_sum, B_poly_sum = '', ''

                                # Construct A with its summands and powers
                                for summand, power in zip(summand_combo_A, powers_A):
                                    matrix = x[power] if summand == 'x' else y[power]
                                    A += matrix
                                    A_poly_sum += f"{summand}{power} + "

                                # Construct B with its summands and powers
                                for summand, power in zip(summand_combo_B, powers_B):
                                    matrix = x[power] if summand == 'x' else y[power]
                                    B += matrix
                                    B_poly_sum += f"{summand}{power} + "

                                # Remove trailing ' + '
                                A_poly_sum = A_poly_sum.rstrip(' + ')
                                B_poly_sum = B_poly_sum.rstrip(' + ')

                                # Transpose matrices A and B
                                AT = np.transpose(A)
                                BT = np.transpose(B)

                                # Construct matrices hx and hz
                                hx = np.hstack((A, B))
                                hz = np.hstack((BT, AT))

                                # Construct and test the CSS code
                                qcode = css_code(hx, hz)  # Define css_code, assuming it's defined elsewhere

                                ### Surpress the print output of the css_code.test()
                                # Redirect stdout to a dummy StringIO object
                                sys.stdout = io.StringIO()

                                if qcode.test():  # Define the test method for qcode
                                    sys.stdout = original_stdout  # Reset stdout to original value to enable logging
                                    r = get_net_encoding_rate(qcode.K, qcode.N)  # Define get_net_encoding_rate
                                    encoding_rate_threshold = 1/15 if encoding_rate_threshold is None else encoding_rate_threshold
                                    if r > encoding_rate_threshold:  # Check your specific criteria for good configurations
                                        code_config = {
                                            'l': l,
                                            'm': m,
                                            'n_phys_qubits': qcode.N,
                                            'n_log_qubits': qcode.K,
                                            'lz': qcode.lz,
                                            'lx': qcode.lx,
                                            'k': qcode.lz.shape[0], 
                                            'encoding_rate': r,
                                            'A_poly_sum': A_poly_sum,
                                            'B_poly_sum': B_poly_sum
                                        }
                                        good_configs.append(code_config)

        except Exception as e:
            logging.warning('An error happened in the parameter space search.', e)
            continue
        
    return good_configs

def calculate_code_distance(
        code_config: Dict
    ) -> int:
    """
    Calculates and returns the code distance for a given code configuration
    """


    # computes the minimum Hamming weight of a binary vector x such that
    # stab @ x = 0 mod 2
    # logicOp @ x = 1 mod 2
    # here stab is a binary matrix and logicOp is a binary vector
    def distance_test(stab, logicOp):
        # number of qubits
        n = stab.shape[1]
        # number of stabilizers
        m = stab.shape[0]

        # maximum stabilizer weight
        wstab = np.max([np.sum(stab[i,:]) for i in range(m)])
        # weight of the logical operator
        wlog = np.count_nonzero(logicOp)
        # how many slack variables are needed to express orthogonality constraints modulo two
        num_anc_stab = int(np.ceil(np.log2(wstab)))
        num_anc_logical = int(np.ceil(np.log2(wlog)))
        # total number of variables
        num_var = n + m*num_anc_stab + num_anc_logical

        model = Model()
        model.verbose = 0
        x = [model.add_var(var_type=BINARY) for i in range(num_var)]
        model.objective = minimize(xsum(x[i] for i in range(n)))

        # orthogonality to rows of stab constraints
        for row in range(m):
            weight = [0]*num_var
            supp = np.nonzero(stab[row,:])[0]
            for q in supp:
                weight[q] = 1
            cnt = 1
            for q in range(num_anc_stab):
                weight[n + row*num_anc_stab +q] = -(1<<cnt)
                cnt+=1
            model+= xsum(weight[i] * x[i] for i in range(num_var)) == 0

        # odd overlap with logicOp constraint
        supp = np.nonzero(logicOp)[0]
        weight = [0]*num_var
        for q in supp:
            weight[q] = 1
        cnt = 1
        for q in range(num_anc_logical):
            weight[n + m*num_anc_stab +q] = -(1<<cnt)
            cnt+=1
        model+= xsum(weight[i] * x[i] for i in range(num_var)) == 1

        model.optimize()

        opt_val = sum([x[i].x for i in range(n)])

        return int(opt_val)
    
    distance = code_config.get('n')
    hx = code_config.get('hx')
    lx = code_config.get('lx')
    for i in range(code_config.get('k')):
        w = distance_test(hx, lx[i, :])
        distance = min(distance, w)

    return distance


def get_code_distance(
        code_configs: List[Dict]
    ):
    """
    Iterate over the list of code configurations and set the code distance attribute
    """
    # Save a pickle for the code configs before the distance calculation (as backup if something does not work in the distance calculation)

    for config in code_configs:
        try:
            config['distance'] = calculate_code_distance(config)
        except:
            config['distance'] = 'FAIL'
            continue    

    return code_configs


if __name__ == '__main__':
    logging.warning('------------------ STARTING CODE SEARCH ------------------')
    # Define the specific values for l, m, and weight
    l_value = range(6, 7) # only the value 6
    m_value = range(6, 7) # only the value 6
    weight_value = range(6, 7) # only the value 6

    # Define the power ranges for summands in A and B
    # Adjust these ranges as per the specific code you're trying to reproduce
    power_range_A = range(1, 4)  # Example range, adjust as needed
    power_range_B = range(1, 4)  # Example range, adjust as needed

    # Call the function with the specific values
    good_configs = search_codes_general(
        l_range=l_value, 
        m_range=m_value, 
        weight_range=weight_value, 
        power_range_A=power_range_A, 
        power_range_B=power_range_B,
        encoding_rate_threshold=1/15,
    )
    save_code_configs(
        my_codes=good_configs,
        file_name='codes_no_distance.pickle'
    )
    # good_configs_with_distance = get_code_distance(good_configs)
    if 'good_configs_with_distance' in globals() and not None:
        save_code_configs(
            my_codes=good_configs,
            file_name='codes_no_distance.pickle'
        )
    logging.warning('------------------ FINISHED CODE SEARCH ------------------')