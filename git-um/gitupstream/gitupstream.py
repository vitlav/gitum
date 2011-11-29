#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# git-um - Git Upstream Manager.
# Copyright (C) 2011  Pavel Shilovsky <piastry@etersoft.ru>
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA.

from git import *
from subprocess import Popen
import os
import tempfile

START_ST = 0
MERGE_ST = 1
REBASE_ST = 2
COMMIT_ST = 3

PULL_FILE = '.git-um-pull'
CONFIG_FILE = '.git-um-config'

class PatchError(Exception):
	def __init__(self, message):
		self.message = message
	def __str__(self):
		return repr(self.message)

class GitUpstream(object):
	def __init__(self, repo_path='.'):
		self._repo = Repo(repo_path)
		self._state = START_ST
		self._id = 0
		self._commits = []
		self._saved_branches = {}

	def pull(self, branch=None):
		self._load_config(CONFIG_FILE)
		if branch:
			self._remote = branch
		if len(self._remote.split('/')) == 2:
			self._repo.git.fetch(self._remote.split('/')[0])
		self._commits = self._get_commits()
		self._commits.reverse()
		self._save_branches()
		self._process_commits()

	def abort(self):
		self._load_config(CONFIG_FILE)
		self._load_state()
		try:
			self._repo.git.rebase('--abort')
		except:
			pass
		self._restore_branches()

	def continue_pull(self, rebase_cmd):
		self._load_config(CONFIG_FILE)
		self._load_state()
		if self._state == REBASE_ST:
			tmp_file = tempfile.TemporaryFile()
			try:
				diff_str = self._stage2(self._commits[self._id], tmp_file, rebase_cmd)
				self._stage3(self._commits[self._id], diff_str)
				self._id += 1
			except GitCommandError as e:
				self._save_state()
				tmp_file.seek(0)
				print ''.join(tmp_file.readlines())
				print(e.stderr)
				return
			except PatchError as e:
				self._save_state()
				print(e.message)
				return
			except:
				self._save_state()
				raise
		elif self._state != MERGE_ST:
			print("Don't support continue not from merge or rebase mode")
			return
		self._process_commits()

	def update_rebased(self, since, to):
		self._load_config(CONFIG_FILE)
		git = self._repo.git
		since = self._repo.commit(since).hexsha
		to = self._repo.commit(to).hexsha
		git.checkout(self._rebased, '-f')
		try:
			for i in [q.hexsha for q in self._repo.iter_commits(since + '..' + to)]:
				git.cherry_pick(i)
		except GitCommandError as e:
			print(e.stderr)
			return
		git.checkout(self._current)

	def create(self, remote, current, upstream, rebased):
		git = self._repo.git

		try:
			self._repo.branches[upstream]
		except:
			self._repo.create_head(upstream)
		try:
			self._repo.branches[current]
		except:
			self._repo.create_head(current)

		try:
			self._repo.delete_head(self._repo.branches[rebased])
		except:
			pass

		git.checkout(current)

		with open(CONFIG_FILE, 'w') as f:
			f.write('remote = %s\n' % remote)
			f.write('current = %s\n' % current)
			f.write('upstream = %s\n' % upstream)
			f.write('rebased = %s\n' % rebased)

		if os.path.exists('.gitignore'):
			f = open('.gitignore', 'a')
		else:
			f = open('.gitignore', 'w')
		f.write('.git-um-*\n')
		f.close()

		git.add('.gitignore')
		git.commit('-m', 'Update .gitignore')

		self._repo.create_head(rebased)

	def _load_config(self, filename):
		try:
			self._load_config_raised(filename)
		except IOError:
			print('.git-um-config missing, using default branch names...')

	def _load_config_raised(self, filename):
		with open(filename, 'r') as f:
			num = 0
			_strs = [q.split('\n')[0] for q in f.readlines()]
			for i in _strs:
				num = 1
				i = i.split('#')[0].strip()
				parts = i.split(' ')
				if len(parts) != 3 or parts[1] != '=':
					print('error in config file on line %d :' % num)
					print('    %s' % i)
					return -1
				if parts[0] == 'upstream':
					self._upstream = parts[2]
				elif parts[0] == 'rebased':
					self._rebased = parts[2]
				elif parts[0] == 'current':
					self._current = parts[2]
				elif parts[0] == 'remote':
					self._remote = parts[2]
		return 0

	def _restore_branches(self):
		git = self._repo.git
		git.checkout(self._upstream, '-f')
		git.reset(self._saved_branches[self._upstream], '--hard')
		git.checkout(self._rebased, '-f')
		git.reset(self._saved_branches[self._rebased], '--hard')
		git.checkout(self._current, '-f')
		git.reset(self._saved_branches[self._current], '--hard')

	def _save_branches(self):
		git = self._repo.git
		self._saved_branches[self._upstream] = self._repo.branches[self._upstream].commit.hexsha
		self._saved_branches[self._rebased] = self._repo.branches[self._rebased].commit.hexsha
		self._saved_branches[self._current] = self._repo.branches[self._current].commit.hexsha

	def _get_commits(self):
		return [q.hexsha for q in self._repo.iter_commits(self._upstream + '..' + self._remote)]

	def _process_commits(self):
		tmp_file = tempfile.TemporaryFile()
		try:
			for i in xrange(self._id, len(self._commits)):
				self._process_commit(self._commits[i], tmp_file)
				self._id += 1
				tmp_file.close()
				tmp_file = tempfile.TemporaryFile()
		except GitCommandError as e:
			self._save_state()
			tmp_file.seek(0)
			print ''.join(tmp_file.readlines())
			print(e.stderr)
		except PatchError as e:
			self._save_state()
			print(e.message)
		except:
			self._save_state()
			raise

	def _process_commit(self, commit, output):
		self._stage1(commit)
		diff_str = self._stage2(commit, output)
		self._stage3(commit, diff_str)

	def _patch_tree(self, diff_str):
		status = 0
		with open('__patch__.patch', 'w') as f:
			f.write(diff_str + '\n')
		with open('__patch__.patch', 'r') as f:
			proc = Popen(['patch', '-p1'], stdin=f)
			status = proc.wait()
		os.unlink('__patch__.patch')
		return status

	def _stage1(self, commit):
		git = self._repo.git
		self._state = MERGE_ST
		git.checkout(self._upstream)
		print('merge commit ' + commit)
		git.merge(commit)

	def _stage2(self, commit, output, rebase_cmd=None):
		git = self._repo.git
		self._state = REBASE_ST
		if rebase_cmd:
			git.rebase(rebase_cmd, output_stream=output)
		else:
			git.checkout(self._rebased)
			self._saved_branches['prev_head'] = self._repo.branches[self._rebased].commit.hexsha
			git.rebase(commit, output_stream=output)
		diff_str = self._repo.git.diff(self._saved_branches['prev_head'], self._rebased)
		return diff_str

	def _stage3(self, commit, diff_str):
		git = self._repo.git
		self._state = COMMIT_ST
		git.checkout(self._current)
		if diff_str == "":
			print('nothing to commit in branch current, skipping %s commit' % commit)
			return
		if self._patch_tree(diff_str) != 0:
			self._id += 1
			self._state = MERGE_ST
			raise PatchError('error occurs during applying the commit %s\n'
					 'fix error, commit and continue the process, please!' % commit)
		git.add('-A')
		mess = self._repo.commit(commit).message
		author = self._repo.commit(commit).author
		git.commit('-m', mess, '--author="%s <%s>"' % (author.name, author.email))

	def _save_state(self):
		with open(PULL_FILE, 'w') as f:
			f.write(self._saved_branches[self._upstream] + '\n')
			f.write(self._saved_branches[self._rebased] + '\n')
			f.write(self._saved_branches[self._current] + '\n')
			f.write(self._saved_branches['prev_head'] + '\n')
			f.write(str(self._state) + '\n')
			for i in xrange(self._id, len(self._commits)):
				f.write(str(self._commits[i]) + '\n')

	def _load_state(self):
		with open(PULL_FILE, 'r') as f:
			self._saved_branches[self._upstream] = f.readline().split()[0]
			self._saved_branches[self._rebased] = f.readline().split()[0]
			self._saved_branches[self._current] = f.readline().split()[0]
			self._saved_branches['prev_head'] = f.readline().split()[0]
			self._state = int(f.readline())
			for i in f.readlines():
				self._commits.append(i.split()[0])
		os.unlink(PULL_FILE)
