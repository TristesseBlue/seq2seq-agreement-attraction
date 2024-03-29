import re
import os
import sys
import csv
import json
import gzip
import random
import shutil
import itertools

from tqdm import tqdm
from typing import *
from functools import partial
from itertools import permutations
from contextlib import suppress

from nltk import PCFG, Tree
from nltk import nonterminals, Nonterminal, Production

ALL_MODELS: Set[str] = {
	f'google/t5-{size}' 
	for size in [
		'efficient-tiny', 
		'efficient-mini', 
		'efficient-small', 
		'efficient-base',
	]
	# + ['large', 'xl', 'xxl']
}

def generate(
	grammar: PCFG, 
	start: str = None, 
	depth: int = None
) -> Tree:
	"""
	Generates an iterator of all sentences from a CFG.

	:param grammar: The Grammar used to generate sentences.
	:param start: The Nonterminal from which to start generate sentences.
	:param depth: The maximal depth of the generated tree.
	:param n: The maximum number of sentences to return.

	:return: a Tree generated by the PCFG.
	"""
	start = grammar.start() if not start else start
	depth = sys.maxsize if depth is None else depth
	items = [start]
	tree  = _generate(grammar, items, depth)
	return tree[0]

def _generate(
	grammar: PCFG, 
	items: List[str], 
	depth: int = None
) -> Tree:
	'''
	Generates a sentence Tree from the passed grammar.
	
	:param grammar: the grammar used to generate a sentence
	:param items: the starting node
	:param depth: the maximum tree depth
	
	:return result: a sentence as a nested list of nodes
	'''
	if depth > 0:
		result = []
		for i in items:
			p = random.random()
			total_rule_prob = 0.
			if isinstance(i, Nonterminal):
				for prod in grammar.productions(lhs=i):
					total_rule_prob += prod.prob()
					if p < total_rule_prob:
						expansion = _generate(grammar, prod.rhs(), depth - 1)
						result += [Tree(i, expansion)]
						break
			else:
				result += [i]
				break
		
		return result

def format_tree_string(
	t: Tree, 
	lang: str = None, 
	pfx: str = None
) -> str:
	"""
	Convert a tree to a string.
	:param t: Tree: an NLTK Tree
	:param lang: str: the name of the language that generate the string (currently unused)
	:param pfx: str: whether the sentence is past or present (currently unused)
	:return: the flattened version of the tree as a string
	"""
	flattened_tree = ' '.join(t.leaves())
	flattened_tree = flattened_tree.strip()
	flattened_tree = flattened_tree.capitalize()
	flattened_tree += '.'
	
	return flattened_tree

def create_data_path(d: str) -> None:
	'''
	Creates a path if one does not exist. Treats final split as a file prefix.
	'''
	split_d = os.path.split(d)
	if len(split_d) > 1:
		if not os.path.isdir(split_d[0]):
			print(f'Creating directory @ "{split_d[0]}"')
			os.makedirs(split_d[0])

def get_labels(t: Tree) -> List[str]:
	'''
	Get the labels of an NLTK tree.
	
	:param t: Tree: the tree whose labels to return
	:returns labels: a list of the labels of the Tree as strings,
					 corresponding to the linear order in which they would be printed.
	'''
	labels = [t.label().symbol()]
	for child in t:
		if isinstance(child, Tree):
			labels.extend(get_labels(child))
	
	return labels

def get_pos_labels(t: Tree) -> List[str]:
	'''
	Get the part-of-speech labels from an NLTK tree.
	This returns only the labels for the terminal nodes.
	
	:param t: Tree: the tree whose labels to return
	:returns labels: a list of the labels of the terminal nodes of the tree as strings,
					 corresponding to the linear order in which they would be printed.
	'''
	labels = []
	for child in t:
		if isinstance(child, Tree) and not isinstance(child[0], str):
			labels.extend(get_pos_labels(child))
		elif isinstance(child, str) or child[0] == '':
			pass
		elif not isinstance(child.label(), str):
			labels.append(child.label().symbol())
		else:
			labels.append(child.label())
	
	return labels

def grep_next_subtree(
	t: Tree,
	expr: str
) -> Tree:
	"""
	Get the next subtree whose label matches the expr.
	The order is determined by height, with higher nodes returned first.
	Note that this is different from the behavior of Tree.subtrees, which returns results
	ordered by linear precedence.
	:param t: Tree: the tree to search.
	:param expr: a regex to search when searching the tree
	:returns Tree: the next highest subtree in t whose label's symbol matches expr
	"""
	positions = sorted(t.treepositions(), key=lambda t: (len(t), t))
	for position in positions:
		if not isinstance(t[position],str) and re.search(expr, str(t[position].label())):
			return t[position]

def get_english_RC_PP_pos_seq(pos_seq: List[str]) -> str:
	'''Remove unwanted info from English pos tags for comparison purposes and return as a string.'''
	pos_seq = [
		pos_tag
			.replace('_sg', '')
			.replace('_pl', '')
		for pos_tag in pos_seq
	]
	pos_seq = '[' + '] ['.join([l for tag in [pos_tag.split() for pos_tag in pos_seq if pos_tag] for l in tag]) + ']'
	
	return pos_seq 

def get_english_RC_PP_example_metadata(
	source: Tree,
	pfx: str,
	target: Tree
) -> Dict:
	"""
	Gets metadata about the passed example, consisting of a seq2seq mapping with a source, prefix, and target.
	:param source: Tree: the source Tree
	:param pfx: str: the task prefix passed to the model
	:param target: the target Tree
	:returns metadata: a dictionary recording the following properties for the example:
					   - transitivity of the main verb (v_trans)
					   - definiteness of main clause subject/object (subj_def, obj_def)
					   - number of main clause subject/object (subj_num, obj_num)
					   - the identity of the main auxiliary (main_aux)
					   - how many adverbial clauses before the main clause
					   - how many adverbial clauses after the main clause
					   - the number of adverbial clauses
					   - the PoS sequence of the source and target
	"""
	source = source.copy(deep=True)
	target = target.copy(deep=True)
	
	metadata = {}
	
	# definiteness of main clause subject
	main_clause_subject = grep_next_subtree(source, r'^DP$')
	main_clause_subject = grep_next_subtree(main_clause_subject, r'^NP$')
	while grep_next_subtree(main_clause_subject[0], r'^NP$'):
		main_clause_subject = grep_next_subtree(main_clause_subject[0], r'^NP$')
	
	main_clause_subject = grep_next_subtree(main_clause_subject, r'^N_')
	
	# number of main clause subject
	if main_clause_subject.label().symbol().endswith('sg'):
		metadata.update({'subject_number': 'sg'})
	else:
		metadata.update({'subject_number': 'pl'})
	
	main_clause_verb_phrase = grep_next_subtree(source, r'^VP$')
	main_clause_object = grep_next_subtree(main_clause_verb_phrase, r'^DP$')
	main_clause_object = grep_next_subtree(main_clause_object, r'^NP$')
	while grep_next_subtree(main_clause_object[0], r'^NP$'):
		main_clause_object = grep_next_subtree(main_clause_object[0], r'^NP$')
	
	main_clause_object = grep_next_subtree(main_clause_object, r'^N_')
		
	# number of main clause object
	if main_clause_object.label().symbol().endswith('sg'):
		metadata.update({'object_number': 'sg'})
	else:
		metadata.update({'object_number': 'pl'})
	
	# main verb
	main_clause_verb = grep_next_subtree(source, r'^V$')
	metadata.update({'main_verb': main_clause_verb[0]})
	
	# number of total, singular, and plural noun phrases between the head noun of the subject and the verb
	main_clause_full_subject = grep_next_subtree(source, r'^DP$')
	main_clause_full_subject = grep_next_subtree(main_clause_full_subject, r'^NP$')
	labels = get_labels(main_clause_full_subject)
	
	intervener_positions = [
		position 
		for position in main_clause_full_subject.treepositions() 
			if  hasattr(main_clause_full_subject[position], '_label') and
				(
					main_clause_full_subject[position].label().symbol().endswith('sg') or 
					main_clause_full_subject[position].label().symbol().endswith('pl')
				)
	][1:]
	
	if intervener_positions:
		final_intervener_number = re.findall(
			r'_(.*)', main_clause_full_subject[intervener_positions[-1]].label().symbol()
		)[0]
		
		metadata.update({'final_intervener_number': final_intervener_number})
	else:
		metadata.update({'final_intervener_number': None})
	
	# then filter to the sg or pl nouns after that
	pre_main_verb_noun_labels = [pos for pos in labels if pos.endswith('sg') or pos.endswith('pl')]
	
	# since the first noun in the list represents the subject, we exclude everything that matches it,
	# since matching nouns are not "distractors"
	distractors = [l for l in pre_main_verb_noun_labels if not l == pre_main_verb_noun_labels[0]]
	
	# subtract one from each to account for the actual head noun, which is not a distractor
	metadata.update({'n_distractors': len(distractors)})
	
	if metadata['n_distractors'] > 0:
		# get the number of the final pre-verb intervening noun (if one exists)
		# if there are any distractors
		# (if they are all the same, there's no way it can be attraction, but we are
		# interested in attraction if there are intermediate distractors)
		# first position is the subject
		
		# are the distractors in RCs or PPs or both?
		distractor_positions = [
			pos 
			for pos in intervener_positions
				if not re.findall(r'^N_(.*)$', str(main_clause_full_subject[pos].label())) == metadata['subject_number']
		]
		
		distractor_path_labels = [
			set([
				str(main_clause_full_subject[path[:i]].label()) 
				for i, _ in enumerate(path)
					if str(main_clause_full_subject[path[:i]].label()) in ['CP', 'PP']
			])
			for path in distractor_positions
		]
		
		distractor_structures = ['both' if len(ls) == 2 else ''.join(ls) for ls in distractor_path_labels]
		
		if distractor_structures == []: breakpoint()
		
		# if we've done all late attachment, it gives us a weird result. since the models aren't getting structures
		# we want to treat this like early attachment, which means rewriting some stuff
		if len(set(distractor_structures)) > 1:
			for i, _ in enumerate(distractor_structures[1:]):
				prev_distractor_structure = distractor_structures[i]
				if distractor_structures[i+1] != prev_distractor_structure:
					distractor_structures[i+1] = 'both'
		
		metadata.update({
			'each_distractor_structure': ','.join(distractor_structures),
			'distractor_structures': 'both' if len(set(distractor_structures)) == 2 else ','.join(set(distractor_structures)),
			'final_distractor_structure': distractor_structures[-1]
		})
	else:
		metadata.update({
			'each_distractor_structure': None,
			'distractor_structures': None,
			'final_distractor_structure': None,
		})
	
	# get pos seq with details suppressed	
	pos_seq = get_english_RC_PP_pos_seq(get_pos_labels(source))
	metadata.update({'pos_sequence': pos_seq})
	
	metadata.update({'tense': pfx})
	
	return metadata

def get_example_metadata(
	grammar: PCFG,
	*args, 
	**kwargs,
) -> Dict:
	"""
	Gets metadata about the passed example, consisting of a seq2seq mapping with a source, prefix, and target.
	:param grammar: the grammar that generated the example
	:param args: passed to get_lang_example_metadata()
	:param kwargs: passed to get_lang_example_metadata()
	:returns metadata: a dictionary recording language-specific properties for the example
	"""
	function_map = {
		'en_RC_PP': get_english_RC_PP_example_metadata,
		'en_RC_PP_gen': get_english_RC_PP_example_metadata,
	}
	
	metadata = function_map.get(grammar.lang, lambda: {})(*args, **kwargs)
	
	return metadata	

def create_dataset_json(
	grammar: PCFG, 
	ex_generator: Callable, 
	file_prefix: str = '',
	overwrite: bool = False,
	**splits: Dict[str,int]
) -> None:
	"""
	Create a dataset json file that can be read using the datasets module's dataset loader.
	Also outputs a companion json that records various linguistic properties of each sentence.
	:param grammar: PCFG: a PCFG object
	:param ex_generator: function: a function that creates a pair of sentences and associated tags from the grammar
	:param file_prefix: str: an identifier to add to the beginning of the output file names
	:param overwrite: bool: whether to overwrite existing datasets with matching names
	:param splits: kwargs mapping a set label to the number of examples to generate for that set
				   ex: train=10000, dev=1000, test=10000
	"""
	file_prefix = file_prefix + '_' if file_prefix and not (file_prefix[-1] in ['-', '_']) else ''
	create_data_path(os.path.join('data', file_prefix))

	for name, n_examples in splits.items():
		metadata = []
		if not os.path.exists(os.path.join('data', file_prefix + name + '.json.gz')) or overwrite:
			prefixes = {}
			l = []
			print(f'Generating {name} examples')
			for n in tqdm(range(n_examples)):
				source, pfx, target = ex_generator(grammar)
				metadata.append(get_example_metadata(grammar, source, pfx, target))
				prefixes[pfx] = 1 if not pfx in prefixes else prefixes[pfx] + 1
				l += [{
					'translation': {
						'src'	: format_tree_string(source, grammar.lang, pfx), 
						'prefix': pfx, 
						'tgt'	: format_tree_string(target, grammar.lang, pfx)
					}
				}]
			
			for pfx in prefixes:
				print(f'{name} prop {pfx} examples: {prefixes[pfx]/n_examples}')
			
			if l:
				print('Saving examples to data/' + file_prefix + name + '.json.gz')
				with gzip.open(os.path.join('data', file_prefix + name + '.json.gz'), 'wt', encoding='utf-8') as f:
					for ex in tqdm(l):
						json.dump(ex, f, ensure_ascii=False)
						f.write('\n')
				
				print('Saving metadata to data/' + file_prefix + name + '_metadata.json.gz')
				with gzip.open(os.path.join('data', file_prefix + name + '_metadata.json.gz'), 'wt', encoding='utf-8') as f:
					for ex in tqdm(metadata):
						json.dump(ex, f, ensure_ascii=False)
						f.write('\n')
			
			print('')
		else:
			print(f'{name} dataset already exists. Skipping. Use overwrite=True to force regeneration.')

def combine_dataset_jsons(
	file_prefix: str = '',
	*files: Tuple[str],
	overwrite: bool = False,
) -> None:
	'''
	Combines dataset jsons.
	:param file_prefix: str: a prefix (without extension) to give to the combine file
	:param *files: Tuple[str]: tuple of strings containing the files to combine 
							   (in the order they should be put into the resulting file)
	:param overwrite: bool: whether to overwrite existing files
	'''
	if not os.path.exists(os.path.join('data', file_prefix + '.json.gz')) or overwrite:
		create_data_path(os.path.join('data', file_prefix))
		
		combined = ''
		for file in files:
			with gzip.open(os.path.join('data', file + ('.json.gz' if not file.endswith('.json.gz') else '')), 'rt', encoding='utf-8') as in_file:
				combined += in_file.read()
		
		with gzip.open(os.path.join('data', file_prefix + '.json.gz'), 'wt', encoding='utf-8') as f:
			f.write(combined)

def create_tense_datasets(
	configs: Dict[str,List] = None, 
	**kwargs
) -> None:
	'''
	Create json datasets according to the passed configs.
	:param configs: (List[Dict]): This should be in the following format:
								   A dict mapping a language id to a dict of datasets.
								   Each dataset maps a label to a list of arguments.
								   Each list of arguments consists of a float, a dict, a PCFG, and an example generator function.
								   The dict maps strings to the number of examples for that split.
								   The float is passed to the ex_generator function, with splits mapping strings to numbers that define how many examples to create for each split
								   	when that float is passed to ex_generator.
								   The PCFG is the grammar from which to generate examples.
								   The example generator function should take the grammar and the probability of generating a present tense example as argument.
								   example:
								  		configs = {
								  			'en_RC_PP': {
								  				'pres': [
								  					0.5, 
								  					{
								  						'train': 100000, 
								  						'dev': 1000, 
								  						'test': 10000
								  					},
								  					english_grammar_RC_PP.english_grammar_RC_PP,  
								  					english_grammar_RC_PP.pres_or_past
								  				]
								  			}
								  		 }
	:param kwargs: passed to create_dataset_json
	If no argument is passed, attempt to load the configs from a file ./data/config.json
	'''
	configs = load_config(configs) if configs is None or isinstance(configs,str) else configs
	
	for lang in configs['langs']:
		for dataset in configs['langs'][lang]:
			print(f'Creating datasets for {lang}-{dataset}')
			p = configs['langs'][lang][dataset][0]
			splits = configs['langs'][lang][dataset][1]
			
			# if we're loading from a file, we have to store these as strings,
			# so we need to import the actual objects
			if (
				isinstance(configs['langs'][lang][dataset][2],str) and 
				isinstance(configs['langs'][lang][dataset][3],str)
			):
				module1 		= configs['langs'][lang][dataset][2].split('.')[0]
				module2 		= configs['langs'][lang][dataset][3].split('.')[0]
				
				exec(f'from core.grammars import {", ".join(set([module1, module2]))}')
				
				grammar 		= eval(configs['langs'][lang][dataset][2])
				ex_generator 	= eval(configs['langs'][lang][dataset][3])
			else:
				grammar 		= configs['langs'][lang][dataset][2]
				ex_generator 	= configs['langs'][lang][dataset][3]
			
			file_prefix = f'{lang}-{dataset}/{lang}-{dataset}'
			p_ex_generator = partial(ex_generator, pres_p=p)
			create_dataset_json(grammar, p_ex_generator, file_prefix, **kwargs, **splits)
			
			print('')

"""
def combine_language_datasets_for_tense(
	langs: List[str], 
	**kwargs
) -> None:
	'''
	Creates dataset jsons for each len 2 permutation of languages passed.
	:param langs: List[str]: a list of language ids corrseponding to directories in ./data/
							  Each language id must have two directories in data associated with it.
							  One is pres_{lang} and the other is past_{lang}.
							  The pres_{lang} directories must contain a file named pres_{lang}_train.json.gz.
							  The past_{lang} directories must contain a file named past_{lang}_train.json.gz.
	
	:outputs: For each possible two-way permutation of languages in langs:
			  a directory in data named pres_{lang1}_{lang2}, with the following datasets jsons.
			  pres_{lang1}_{lang2}_train.json.gz, containing positive-positive/presative training examples from lang1 
			  	and positive-positive training examples from lang2.
	'''
	langs = list(load_config(langs).keys()) if langs is None or isinstance(langs,str) else langs
	
	all_pairs = permutations(langs, 2)
	for lang1, lang2 in all_pairs:
		print(f'Creating datasets for {lang1} -> {lang2}')
		dirname 	= f'pres_{lang1}_{lang2}'
		file_prefix = os.path.join(dirname, f'pres_{lang1}_{lang2}_train')
		
		# create the training dataset with past-pres/past examples from lang1 and past-past examples from lang2
		combine_dataset_jsons(
			file_prefix, 
			os.path.join(f'pres_{lang1}', f'pres_{lang1}_train.json.gz'), 
			os.path.join(f'pos_{lang2}', f'pos_{lang2}_train.json.gz'),
			**kwargs
		)
		
		print('')
"""

def create_datasets_from_config(
	configs: Dict[str,List] = None, 
	**kwargs
) -> None:
	'''
	Create and then combine tense datasets for each combination of languages in configs.keys().
	
	:param configs: Dict[str,List]: passed to create_tense_datasets
	:param kwargs: passed to create_tense_datasets, 
				   combine_language_datasets_for_tense,
				   and create_scripts
	 			   (useful to set overwrite=True)
	
	:outputs: see outputs of create_tense_datasets and combine_language_datasets_for_tense.
	'''
	configs = load_config(configs) if configs is None or isinstance(configs,str) else configs
	
	create_tense_datasets(configs, **kwargs)
	# combine_language_datasets_for_tense(list(configs.keys()), **kwargs)
	create_scripts(configs, **kwargs)

def create_scripts(
	configs: Dict = None, 
	overwrite: bool = False
) -> None:
	'''
	Creates finetuning and eval scripts for the passed configs for the models in ALL_MODELS.
	
	:params langs: (List[str]): a list of language abbreviations with files in the ./data/ directory.
	
	If no argument is passed, attempt to load the language ids from a file ./data/config.json
	'''	
	script = '\n'.join([
		'#!/bin/bash\n',
		'#SBATCH --job-name=[MODEL]-finetune-tense-[TRAIN_LANG]',
		'#SBATCH --output=joblogs/%x_%j.txt',
		'#SBATCH --nodes=1',
		'#SBATCH --cpus-per-task=1',
		'#SBATCH --mem=30GB',
		'#SBATCH --time=10:00:00',
		'#SBATCH --gpus=v100:1',
		'#SBATCH --partition=gpu',
		'#SBATCH --mail-type=END,FAIL,INVALID_DEPEND',
		'',
		'module load CUDA',
		'module load cuDNN',
		'module load miniconda',
		'',
		'source activate /gpfs/gibbs/project/frank/ref4/conda_envs/py38-agratt',
		'',
		'python core/run_seq2seq.py \\',
		"	--model_name_or_path '[MODEL_NAME_OR_PATH]' \\",
		'	--do_train \\',
		'	--task translation_src_to_tgt \\',
		'	--train_file data/[TRAIN_LANG]/[TRAIN_LANG]_train.json.gz \\',
		'	--validation_file data/[DEV_LANG]/[DEV_LANG]_dev.json.gz \\',
		'	--output_dir outputs/[MODEL]-finetuning-[TRAIN_LANG]-bs128/ \\',
		'	--per_device_train_batch_size=4 \\',
		'	--gradient_accumulation_steps=32 \\',
		'	--per_device_eval_batch_size=16 \\',
		'	--overwrite_output_dir \\',
		'	--predict_with_generate \\',
		'	--num_train_epochs 10.0'
	]) + '\n'
	
	eval_script = script.replace('finetune', 'eval')
	eval_script = eval_script.replace('--do_train \\', '--do_learning_curve \\')
	eval_script = eval_script.replace('[DEV_LANG]', '[TEST_LANG]')
	eval_script = re.sub(r'_dev(\.|_)', '_test\\1', eval_script)
	eval_script = eval_script.replace('--per_device_train_batch_size=4', '--per_device_train_batch_size=8')
	eval_script = eval_script.replace('	--gradient_accumulation_steps=32 \\\n', '')
	eval_script = eval_script.replace(
		'	--predict_with_generate \\\n	--num_train_epochs 10.0', 
		'	--predict_with_generate \\'
	)
	
	configs 	= load_config() if configs is None else configs
	all_pairs 	= [tuple(pair) for pair in configs['pairs']] if 'pairs' in configs else []
	langs 		= [(f'{lang}-{dataset}',f'{lang}-{dataset}') for lang in configs['langs'] for dataset in configs['langs'][lang]] + all_pairs
	
	# create directories if not existant
	os.makedirs(os.path.join('scripts', 'finetune'), exist_ok=True)
	os.makedirs(os.path.join('scripts', 'eval'), exist_ok=True)
	
	for lang in langs:
		for model in ALL_MODELS:
			# create the scripts for each language and pair of languages
			lang_ft_script = script.replace('[MODEL_NAME_OR_PATH]', model)
			lang_ft_script = lang_ft_script.replace('[MODEL]', model.split('/')[-1])
			lang_ev_script = eval_script.replace('[MODEL_NAME_OR_PATH]', model)
			lang_ev_script = lang_ev_script.replace('[MODEL]', model.split('/')[-1])
			
			train_lang 		= lang[0]
			dev_lang 		= lang[0]
			# train_dash_lang = lang[0].replace('_', '-')
			test_lang 		= lang[1]
			
			file_name 		= '_'.join(lang) if lang[0] != lang[1] else lang[0]
			
			if os.path.isfile(os.path.join('data', train_lang, f'{train_lang}_train.json.gz')):
				print(f'Creating scripts for {" -> ".join(lang)} ({model})')
				# if the langs are not the same, we do not need to create a separate tuning script, only a separate eval script
				if (
					lang[0] == lang[1] and 
					os.path.isfile(os.path.join('data', dev_lang, f'{dev_lang}_dev.json.gz'))
				):
					lang_ft_script = lang_ft_script.replace('[TRAIN_LANG]', train_lang)
					lang_ft_script = lang_ft_script.replace('[DEV_LANG]', dev_lang)
					# lang_ft_script = lang_ft_script.replace('[TRAIN-LANG]', train_dash_lang)
					if not os.path.exists(os.path.join('scripts', 'finetune', f'finetune_{model.split("/")[-1]}_{file_name}_bs128.sh')) or overwrite:
						with open(os.path.join('scripts', 'finetune', f'finetune_{model.split("/")[-1]}_{file_name}_bs128.sh'), 'wt') as out_file:
							out_file.write(lang_ft_script)
				
				if os.path.isfile(os.path.join('data', test_lang, f'{test_lang}_test.json.gz')):
					lang_ev_script = lang_ev_script.replace('[TRAIN_LANG]', train_lang)
					lang_ev_script = lang_ev_script.replace('[TEST_LANG]', test_lang)
					# lang_ev_script = lang_ev_script.replace('[TRAIN-LANG]', train_dash_lang)
					if not os.path.exists(os.path.join('scripts', 'eval', f'eval_{model.split("/")[-1]}_{file_name}_bs128.sh')) or overwrite:
						with open(os.path.join('scripts', 'eval', f'eval_{model.split("/")[-1]}_{file_name}_bs128.sh'), 'wt') as out_file:
							out_file.write(lang_ev_script)
					
			"""
			# if we have multiple languages, create a zero-shot version of the eval script
			if len(lang) == 2:
				lang_zs_ev_script 	= eval_script.replace(
					f'#SBATCH --job-name={model}-eval-pres-[TRAIN-LANG]',
					'f#SBATCH --job-name={model}-eval-pres-[TRAIN-ZS-LANG]-zs'
				)
				train_lang 			= lang[0]
				train_dash_lang 	= lang[0]
				
				lang_zs_ev_script 	= lang_zs_ev_script.replace('[TRAIN_LANG]', train_lang)
				lang_zs_ev_script 	= lang_zs_ev_script.replace('[TEST_LANG]', test_lang)
				lang_zs_ev_script 	= lang_zs_ev_script.replace('[TRAIN-ZS-LANG]', '-'.join(lang))
				lang_zs_ev_script 	= lang_zs_ev_script.replace('[TRAIN-LANG]', train_dash_lang)
				
				if not os.path.exists(os.path.join('scripts', 'eval', f'eval_{model}_pres_{"_".join(lang)}_bs128_zs.sh')) or overwrite:
					with open(os.path.join('scripts', 'eval', f'eval_{model}_pres_{"_".join(lang)}_bs128_zs.sh'), 'wt') as out_file:
						out_file.write(lang_zs_ev_script)
			"""
	
def load_config(path: 'str or Pathlike' = None) -> Dict[str,List]:
	'''
	Loads a dataset creation config file from disk.
	
	:param path: str or Pathlike: the path to the config file.
						   If no path is provided, attempt to load
						   ./data/config.json as the config.
	'''
	if path is None:
		path = os.path.join('data', 'config.json')
	
	with open(path, 'rt', encoding='utf-8') as in_file:
		configs = json.load(in_file)
	
	return configs