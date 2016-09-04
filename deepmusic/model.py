#!/usr/bin/env python3

# Copyright 2015 Conchylicultor. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ==============================================================================

"""
Model to generate new songs

"""

import tensorflow as tf

from deepmusic.musicdata import Batch
import deepmusic.songstruct as music


class Model:
    """
    Base class which manage the different models and experimentation.
    """
    
    def __init__(self, args):
        """
        Args:
            args: parameters of the model
        """
        print('Model creation...')

        self.args = args  # Keep track of the parameters of the model

        # Placeholders
        self.inputs = None
        self.targets = None

        # Main operators
        self.opt_op = None  # Optimizer
        self.outputs = None  # Outputs of the network
        self.final_state = None  # When testing, we feed this value as initial state ?

        # Construct the graphs
        self._build_network()

    def _build_network(self):
        """ Create the computational graph
        """

        # Placeholders (Use tf.SparseTensor with training=False instead)
        with tf.name_scope('placeholder_inputs'):
            self.inputs = [
                tf.placeholder(
                    tf.float32,  # -1.0/1.0 ? Probably better for the sigmoid
                    [self.args.batch_size, music.NB_NOTES], name='input') for _ in range(self.args.sample_length)
                ]
        with tf.name_scope('placeholder_targets'):
            self.targets = [
                tf.placeholder(
                    tf.float32,  # 0/1
                    [self.args.batch_size, music.NB_NOTES], name='target') for _ in range(self.args.sample_length)
                ]

        # Projection
        with tf.variable_scope('note_projection'):  # TODO: Make sure reuse is True
            W = tf.get_variable(
                'weights',
                [self.args.hidden_size, music.NB_NOTES],
                initializer=tf.truncated_normal_initializer()
            )
            b = tf.get_variable(
                'bias',
                [music.NB_NOTES],
                initializer=tf.truncated_normal_initializer()  # Tune the initializer ?
            )

            def project_note(X):
                return tf.matmul(X, W) + b  # [batch_size, NB_NOTE]

        # RNN network
        with tf.variable_scope('rnn_cell'):  # TODO: How to make this appear on the graph ?
            rnn_cell = tf.nn.rnn_cell.BasicLSTMCell(self.args.hidden_size, state_is_tuple=True)  # Or GRUCell, LSTMCell(args.hidden_size)
            #rnn_cell = tf.nn.rnn_cell.DropoutWrapper(rnn_cell, input_keep_prob=1.0, output_keep_prob=1.0)  # TODO: Custom values (WARNING: No dropout when testing !!!)
            rnn_cell = tf.nn.rnn_cell.MultiRNNCell([rnn_cell] * self.args.num_layers, state_is_tuple=True)

        initial_state = rnn_cell.zero_state(batch_size=self.args.batch_size, dtype=tf.float32)

        def loop_rnn(prev, i):
            """ Loop function used to connect one output of the rnn to the next input.
            Will re-adapt the output shape to the input one.
            This is useful to use the same network for both training and testing. Warning: Because of the fixed
            batch size, we have to predict batch_size sequences when testing.
            """
            # TODO: Implement Samy Bengio training trick
            # TODO: Make the prediction for each note (with a scaled sigmoid: 2*sig - 1 = +/- 1
            if self.args.test:
                # Predict the output from prev and scale the result on [-1, 1]
                out = project_note(prev)
                out = tf.sub(tf.mul(2.0, tf.nn.sigmoid(out)), 1.0)  # x_{i} = 2*sigmoid(y_{i-1}) - 1
                return out
            else:
                return self.inputs[i]

        (outputs, self.final_state) = tf.nn.seq2seq.rnn_decoder(
            decoder_inputs=self.inputs,
            initial_state=initial_state,
            cell=rnn_cell,
            loop_function=loop_rnn  # TODO: WARNING!!! INCOMPATIBLE WITH MULTI RNN ??
        )

        # Final projection
        with tf.name_scope('final_output'):
            self.outputs = []
            for output in outputs:
                proj = project_note(output)
                self.outputs.append(proj)

        # For training only
        if not self.args.test:
            # Finally, we define the loss function

            # The network will predict a mix a wrong and right notes. For the loss function, we would like to
            # penalize note which are wrong. Eventually, the penalty should be less if the network predict the same
            # note but not in the right pitch (ex: C4 instead of C5), with a decay the further the prediction
            # is (D5 and D1 more penalized than D4 and D3 if the target is D2)

            # For now, by using sigmoid_cross_entropy_with_logits, the task is formulated as a NB_NOTES binary
            # classification problems

            loss_fct = tf.nn.seq2seq.sequence_loss(  # Or sequence_loss_by_example ??
                self.outputs,
                self.targets,
                [tf.ones(self.targets[0].get_shape()) for _ in range(len(self.targets))],  # Weights
                softmax_loss_function=tf.nn.sigmoid_cross_entropy_with_logits
            )
            tf.scalar_summary('training_loss', loss_fct)  # Keep track of the cost

            # Initialize the optimizer
            opt = tf.train.AdamOptimizer(
                learning_rate=self.args.learning_rate,
                beta1=0.9,
                beta2=0.999,
                epsilon=1e-08
            )
            self.opt_op = opt.minimize(loss_fct)
    
    def step(self, batch):
        """ Forward/training step operation.
        Does not perform run on itself but just return the operators to do so. Those have then to be run
        Args:
            batch (Batch): Input data on testing mode, input and target on output mode
        Return:
            (ops), dict: A tuple of the (training,) operator or (outputs,) in testing mode with the associated feed dictionary
        """

        # Feed the dictionary
        feed_dict = {}
        ops = None

        if not self.args.test:  # Training
            # Feed placeholder
            for i in range(self.args.sample_length):
                feed_dict[self.inputs[i]] = batch.inputs[i]
                feed_dict[self.targets[i]] = batch.targets[i]

            ops = (self.opt_op,)
        else:  # Testing (batch_size == 1)
            # Feed placeholder
            # TODO: What to put for initialisation state (empty ? random ?) ?
            feed_dict[self.inputs[0]] = batch.inputs[0]

            ops = (self.outputs,)

        # Return one pass operator
        return ops, feed_dict
