#
# Copyright (c) 2017 Intel Corporation 
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

from typing import Union

import numpy as np

from rl_coach.agents.categorical_dqn_agent import CategoricalDQNNetworkParameters, CategoricalDQNAlgorithmParameters, \
    CategoricalDQNAgent, CategoricalDQNAgentParameters
from rl_coach.agents.dqn_agent import DQNNetworkParameters, DQNAlgorithmParameters
from rl_coach.agents.value_optimization_agent import ValueOptimizationAgent
from rl_coach.architectures.tensorflow_components.heads.categorical_q_head import CategoricalQHeadParameters
from rl_coach.base_parameters import AgentParameters
from rl_coach.exploration_policies.parameter_noise import ParameterNoiseParameters
from rl_coach.memories.non_episodic.experience_replay import ExperienceReplayParameters
from rl_coach.memories.non_episodic.prioritized_experience_replay import PrioritizedExperienceReplayParameters, \
    PrioritizedExperienceReplay
from rl_coach.schedules import LinearSchedule

from rl_coach.core_types import StateType
from rl_coach.exploration_policies.e_greedy import EGreedyParameters


class RainbowDQNNetworkParameters(CategoricalDQNNetworkParameters):
    def __init__(self):
        super().__init__()


class RainbowDQNAlgorithmParameters(CategoricalDQNAlgorithmParameters):
    def __init__(self):
        super().__init__()


class RainbowDQNExplorationParameters(ParameterNoiseParameters):
    def __init__(self, agent_params):
        super().__init__(agent_params)


class RainbowDQNAgentParameters(CategoricalDQNAgentParameters):
    def __init__(self):
        super().__init__()
        self.algorithm = RainbowDQNAlgorithmParameters()
        self.exploration = RainbowDQNExplorationParameters(self)
        self.memory = PrioritizedExperienceReplayParameters()
        self.network_wrappers = {"main": RainbowDQNNetworkParameters()}

    @property
    def path(self):
        return 'rl_coach.agents.rainbow_dqn_agent:RainbowDQNAgent'


# Rainbow Deep Q Network - https://arxiv.org/abs/1710.02298
# Agent implementation is WIP. Currently has:
# 1. DQN
# 2. C51
# 3. Prioritized ER
# 4. DDQN
#
# still missing:
# 1. N-Step
# 2. Dueling DQN
class RainbowDQNAgent(CategoricalDQNAgent):
    def __init__(self, agent_parameters, parent: Union['LevelManager', 'CompositeAgent']=None):
        super().__init__(agent_parameters, parent)

    def learn_from_batch(self, batch):
        network_keys = self.ap.network_wrappers['main'].input_embedders_parameters.keys()

        ddqn_selected_actions = np.argmax(self.distribution_prediction_to_q_values(
            self.networks['main'].online_network.predict(batch.next_states(network_keys))), axis=1)

        # for the action we actually took, the error is calculated by the atoms distribution
        # for all other actions, the error is 0
        distributed_q_st_plus_1, TD_targets = self.networks['main'].parallel_prediction([
            (self.networks['main'].target_network, batch.next_states(network_keys)),
            (self.networks['main'].online_network, batch.states(network_keys))
        ])

        # only update the action that we have actually done in this transition (using the Double-DQN selected actions)
        target_actions = ddqn_selected_actions
        m = np.zeros((self.ap.network_wrappers['main'].batch_size, self.z_values.size))

        batches = np.arange(self.ap.network_wrappers['main'].batch_size)
        for j in range(self.z_values.size):
            tzj = np.fmax(np.fmin(batch.rewards() +
                                  (1.0 - batch.game_overs()) * self.ap.algorithm.discount * self.z_values[j],
                                  self.z_values[self.z_values.size - 1]),
                          self.z_values[0])
            bj = (tzj - self.z_values[0])/(self.z_values[1] - self.z_values[0])
            u = (np.ceil(bj)).astype(int)
            l = (np.floor(bj)).astype(int)
            m[batches, l] = m[batches, l] + (distributed_q_st_plus_1[batches, target_actions, j] * (u - bj))
            m[batches, u] = m[batches, u] + (distributed_q_st_plus_1[batches, target_actions, j] * (bj - l))

        # total_loss = cross entropy between actual result above and predicted result for the given action
        TD_targets[batches, batch.actions()] = m

        # update errors in prioritized replay buffer
        importance_weights = batch.info('weight') if isinstance(self.memory, PrioritizedExperienceReplay) else None

        result = self.networks['main'].train_and_sync_networks(batch.states(network_keys), TD_targets,
                                                               importance_weights=importance_weights)

        total_loss, losses, unclipped_grads = result[:3]

        # TODO: fix this spaghetti code
        if isinstance(self.memory, PrioritizedExperienceReplay):
            errors = losses[0][np.arange(batch.size), batch.actions()]
            self.memory.update_priorities(batch.info('idx'), errors)

        return total_loss, losses, unclipped_grads
