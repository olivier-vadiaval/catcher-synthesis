"""
sim_anneal.py

Author: Olivier Vadiavaloo

Description:
This file contains the code implementing the simulated annealing
algorithm.

"""
import copy as cp
import time
import random
import multiprocessing as mp
from math import exp
from src.dsl import *
from src.evaluation import *
from src.Optimizer.optimizer import *
from src.SA.plotter import *

class SimulatedAnnealing:

    def __init__(self, time_limit, logger, run_optimizer):
        self.time_limit = time_limit
        self.is_triage = run_optimizer['triage']
        self.run_optimizer = run_optimizer['run_optimizer']
        self.n_iter = run_optimizer['iterations']
        self.kappa = run_optimizer['kappa']
        self.is_parallel = run_optimizer['parallel']
        self.logger = logger

    def get_terminal_node(self, p, valid_ith_child_types):
        terminal_nodes = []

        for child_type in valid_ith_child_types:
            if child_type is None:
                terminal_nodes.append(child_type)
                continue

            child = Node.instance(child_type)
            if child_type in [VarFromArray.className(), VarScalar.className(), Constant.className()] \
                or child.get_max_number_children() == 0:
                terminal_nodes.append(child)

        if terminal_nodes == 0:
            for child_type in valid_ith_child_types:
                child = Node.instance(child_type)

                if child.get_max_children_number() == 1:
                    terminal_nodes.append(child)

        if len(terminal_nodes) > 0:
            return random.choice(terminal_nodes)

        return Node.instance(random.choice(list(valid_ith_child_types)))

    def complete_program(self, p, depth, max_depth, max_size):
        if p is None:
            return

        for i in range(p.get_max_number_children()):
            valid_ith_child_types = p.get_valid_children_types()[i]

            # if p is a scalar or constant, no need to call complete_program on child
            if isinstance(p, VarScalar) or isinstance(p, VarFromArray) or isinstance(p, Constant):
                child = random.choice(list(valid_ith_child_types))
                p.add_child(child)

            # if max depth is exceeded, get a terminal node
            elif depth >= max_depth or p.get_size() >= max_size:
                child = self.get_terminal_node(p, valid_ith_child_types)
                p.add_child(child)
                self.complete_program(child, depth+1, max_depth, max_size)

            # else choose a random child node
            else:
                child = Node.instance(random.choice(list(valid_ith_child_types)))
                p.add_child(child)
                self.complete_program(child, depth+1, max_depth, max_size)

    def generate_random(self):
        initial_nodes = Node.get_valid_children_types()[0]
        random_p = Node.instance(random.choice(list(initial_nodes)))
        self.complete_program(random_p, self.initial_depth, self.max_depth, self.max_size)
        random_p.check_correct_size()
        return random_p

    def mutate_inner_nodes(self, p, index):
        self.processed_nodes += 1

        if not isinstance(p, Node):
            return False

        for i in range(p.get_max_number_children()):

            if index == self.processed_nodes:
                valid_ith_child_types = p.get_valid_children_types()[i]
                
                child = Node.instance(random.choice(list(valid_ith_child_types)))

                if isinstance(child, Node):
                    self.complete_program(child, self.initial_depth, self.max_depth, self.max_size-p.get_size())
                p.replace_child(child, i)

                return True

            if self.mutate_inner_nodes(p.get_children()[i], index):
                return True

        return False

    def mutate(self, p):
        # print('p.get_size()', p.get_size())
        index = random.randint(0, p.get_size())
        # print('index', index)
        # print()

        # root will be mutated
        if index == 0:
            ptypes = Node.get_valid_children_types()[0]
            p = Node.instance(random.choice(list(ptypes)))
            self.complete_program(p, self.initial_depth, self.max_depth, self.max_size)
            p.check_correct_size()

            return p

        self.processed_nodes = 0
        self.mutate_inner_nodes(p, index)
        p.check_correct_size()

        return p

    def reduce_temp(self, current_t, epoch):
        return current_t / (1 + self.alpha * epoch)

    def is_accept(self, j_diff, temp):
        rand = random.uniform(0, 1)
        if rand < min(1, exp(j_diff * (self.beta / temp))):
            return True
        return False

    def init_var_child_types(self, grammar):
        VarFromArray.valid_children_types = [set(grammar['arrays']), set(grammar['array_indexes'])]
        VarScalar.valid_children_types = [set(grammar['scalars'])]
        Constant.valid_children_types = [set(grammar['constants'])]

    def start_optimizer(self, ppool):
        if self.is_parallel:
            with mp.Pool() as pool:
                current_best = None
                current_best_score = -1000000
                for arg, res in zip(ppool, pool.starmap(self.optimizer.optimize, ppool, chunksize=20)):
                    optimized_p, optimized_const_values, new_score, is_optimized = res
                    arg_p, arg_pscore = arg

                    if is_optimized:
                        pdescr = {'header': 'Optimized Program', 'psize': optimized_p.get_size(), 'score': new_score}
                        self.logger.log('Constant Values: ' + str(optimized_const_values))
                        self.logger.log_program(optimized_p.to_string(indent=1), pdescr)
                        self.logger.log('Previous Score: ' + str(arg_pscore), end='\n\n')
                        
                    if new_score > current_best_score:
                        current_best_score = new_score
                        current_best = optimized_p

                return current_best, current_best_score

        else:
            p, pscore = ppool[0]
            optimized_p, optimized_const_values, new_score, is_optimized = self.optimizer.optimize(p, pscore)
            
            if is_optimized:
                pdescr = {'header': 'Optimized Program', 'psize': optimized_p.get_size(), 'score': new_score}
                self.logger.log('Constant Values: ' + str(optimized_const_values))
                self.logger.log_program(optimized_p.to_string(indent=1), pdescr)
                self.logger.log('Previous Score: ' + str(pscore), end='\n\n')
            
            return optimized_p, new_score

    def synthesize(self, grammar, current_t, final_t, eval_funct,
        option=1, verbose_opt=False, generate_plot=False):
        """
        This method implements the simulated annealing algorithm that can be used
        to generate strategies given a grammar and an evaluation function.

            - CFG: grammar
            - current_t: initial temperature
            - final_t: final temperature
            - option: 1 or 2
                -- Option 1: Does not generate a random program each time simulated annealing
                             finishes to run. More likely to get stuck on a local min/max.
                -- Option 2: Generates a random program after each simulated annealing run.

        """
        start = time.time()
        self.logger.set_start(start)

        self.alpha = 0.9
        self.beta = 100
        self.initial_depth = 0
        self.max_depth = 4
        self.max_size = 50
        self.initial_t = current_t

        # Initialize variables used to generate plots later on
        scores_dict = {}
        best_pscore_dict = {}

        self.init_var_child_types(grammar)

        if self.run_optimizer:
            self.optimizer = Optimizer(eval_funct, self.is_triage, self.n_iter, self.kappa)

        iterations = 0
        ppool = []

        if self.is_parallel:
                ppool_max_size = 5
        else:
            ppool_max_size = 1

        # Option 2: Generate random program only once
        if option == 2:
            best = self.generate_random()
            scores, best_eval = eval_funct.evaluate(best, verbose=True)
            
        else:
            best = None
            best_eval = None

        '''
        Run Simulated Annealing until time limit is reached
        If option 1 is specified, generate a random program for
        the initial program. If option 2 is specified, use best
        as the initial program.
        '''
        while time.time() - start < self.time_limit:
            current_t = self.initial_t

            # Option 1: Generate random program and compare with best
            if option == 1:
                current = self.generate_random()
                scores, current_eval = eval_funct.evaluate(current, verbose=True)

                if best is None or current_eval > best_eval:
                    best, best_eval = current, current_eval
            
            # Option 2: Assign current to best solution in previous iteration
            elif option == 2 and best is not None:
                current = best
                current_eval = best_eval

            # Log initial program to file
            pdescr = {'header': 'Initial Program', 'psize': current.get_size(), 'score': current_eval}
            self.logger.log_program(current.to_string(), pdescr)
            self.logger.log('Scores: ' + str(scores).strip('()'), end='\n\n')

            # Store score values to generate plot before exiting if needed
            scores_dict[iterations] = {}
            best_pscore_dict[iterations] = {}

            if current_eval != -1_000_000:
                scores_dict[iterations][0] = current_eval

            if best_eval != -1_000_000:
                best_pscore_dict[iterations][0] = best_eval

            epoch = 0
            mutations = 0    
            while current_t > final_t:
                best_updated = False
                header = 'Mutated Program'

                # Mutate current program
                candidate = self.mutate(cp.deepcopy(current))
                mutations += 1

                # Evaluate the mutated program
                scores, candidate_eval = eval_funct.evaluate(candidate, verbose=True)

                # If candidate program does not raise an error
                if candidate_eval != -1_000_000:
                    scores_dict[iterations][epoch+1] = candidate_eval

                # Run optimizer if flag was specified
                if self.run_optimizer:
                    ppool.append((candidate, candidate_eval))
                    # print('ppool_len', len(ppool))

                    if len(ppool) >= ppool_max_size:
                        candidate, candidate_eval = self.start_optimizer(ppool)
                        ppool = []

                        # if optimized_candidate_eval > candidate_eval:
                        #     candidate = optimized_candidate
                        #     candidate_eval = optimized_candidate_eval

                # Update the current best program if needed
                if candidate_eval > best_eval:
                    header = 'New Best Program'
                    best_updated = True
                    best, best_eval = candidate, candidate_eval
                    best_pscore_dict[iterations][epoch+1] = best_eval

                # Log program to file
                if best_updated or verbose_opt:
                    pdescr = {'header': header, 'psize': candidate.get_size(), 'score': candidate_eval}
                    self.logger.log_program(candidate.to_string(), pdescr)
                    self.logger.log('Scores: ' + str(scores).strip('()'))
                    self.logger.log('Mutations: ' + str(mutations), end='\n\n')

                j_diff = candidate_eval - current_eval
                
                if j_diff > 0 or self.is_accept(j_diff, current_t):
                    current, current_eval = candidate, candidate_eval
                
                current_t = self.reduce_temp(current_t, epoch)
                epoch += 1

            iterations += 1
            self.logger.log('Total iterations: ' + str(iterations), end='\n\n')

        self.logger.log('Running Time: ' + str(round(time.time() - start, 2)) + 'seconds')

        # Log best program
        pdescr = {'header': 'Best Program Found By SA', 'psize': best.get_size(), 'score': best_eval}
        self.logger.log_program(best.to_string(), pdescr)

        if generate_plot:
            plotter = Plotter()
            plot_names = {
                'x': 'iterations',
                'y': 'Program Score',
                'title': 'Simulated Annealing Program Score Graph',
                'legend': ['current program', 'best program']
            }

            plotter.plot_from_data(scores_dict, best_pscore_dict, names=plot_names)
            plotter.save_data(scores_dict, best_pscore_dict, names=['all_scores.dat', 'best_scores.dat'])
            # plotter.plot_from_file('data/all_scores.dat', names=plot_names)

        return best, best_eval