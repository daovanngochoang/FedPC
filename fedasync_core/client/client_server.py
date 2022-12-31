from abc import ABC, abstractmethod

import numpy as np
from pika import BlockingConnection
from pika.adapters.blocking_connection import BlockingChannel
from pika.spec import Basic, BasicProperties
from fedasync_core.commons.utils.time_helpers import time_now
from fedasync_core.commons.config import QueueConfig, RoutingRules, ServerConfig
from fedasync_core.commons.utils.message_helper import *
from fedasync_core.commons.utils.awss3_file_manager import AwsS3
import uuid
from fedasync_core.commons.utils.numpy_file_helpers import *


class ClientServer(ABC):

    def __init__(self, n_epochs: int, queue_connection: BlockingConnection) -> None:
        self.connection: BlockingConnection = queue_connection

        # never change
        self.id: str = str(uuid.uuid4())
        self.prefix = str(uuid.uuid4())

        self.weight_file: str = "{}_{}.weight".format(self.prefix, self.id)
        self.bias_file = "{}_{}.bias".format(self.prefix, self.id)

        self.model = None

        self.client_epoch: int = 0
        self.channel: BlockingChannel = self.connection.channel()
        self.n_epochs: int = n_epochs
        self.acc: float = 0.0
        self.loss: float = 0.0
        self.start: str = ""
        self.end: str = ""
        self.awss3 = AwsS3()

    def start_listen(self) -> None:
        """Listen to training events
        """

        # send register
        self.send_to_server(RoutingRules.CLIENTS_REGISTER, self.id)

        while True:
            method_frame: Basic.GetOk
            header_frame: BasicProperties
            method_frame, header_frame, body = self.channel.basic_get(QueueConfig.CLIENT_QUEUE)

            # close channel to avoid blocking then reconnect
            self.channel.close()
            self.channel = self.connection.channel()

            if method_frame:

                # decode
                global_msg: GlobalMessage = decode_global_msg(body)

                print(global_msg.current_epoch)
                print(global_msg.chosen_id)
                print(global_msg.chosen_id)

                # if the all epochs complete => release and break
                if global_msg.n_epochs - global_msg.current_epoch == 0:
                    self.channel.close()
                    self.connection.close()
                    break

                # if client epoch is smaller than global epoch => train
                if self.client_epoch < global_msg.current_epoch and self.id in global_msg.chosen_id:
                    print("start local training")
                    self.start = time_now()

                    self.create_model()

                    self.awss3.download_awss3_file(global_msg.weight_file)
                    self.awss3.download_awss3_file(global_msg.bias_file)

                    weight = load_array(self.awss3.tmp + global_msg.weight_file)
                    bias = load_array(self.awss3.tmp + global_msg.weight_file)

                    model_weights = [weight, bias]
                    self.model.set_weights(model_weights)

                    self.data_preprocessing()

                    print("Fit")
                    # train
                    self.fit()

                    # eval
                    print("Evaluate")
                    self.evaluate()

                    # upload to aws s3 first.
                    self.awss3.upload_file_to_awss3(self.weight_file)
                    self.awss3.upload_file_to_awss3(self.bias_file)

                    # get the end time
                    self.end = time_now()

                    # Generate update msg
                    update_msg = UpdateMessage(
                        client_id=self.id, epoch=self.client_epoch,
                        weight_file=self.weight_file, bias_file=self.bias_file,
                        acc=self.acc, loss=self.loss, start=self.start
                    )

                    # Encode and send
                    encoded_update_msg = encode_update_msg(update_msg)

                    self.send_to_server(RoutingRules.LOCAL_UPDATE, encoded_update_msg)

                    # reset data
                    self.client_epoch += 1
                    self.start = ""
                    self.end = ""

    def send_to_server(self, routing_key: str, body):
        """Send msg to server
        """
        self.channel.basic_publish(
            exchange=QueueConfig.EXCHANGE,
            routing_key=routing_key,
            body=body)

    def save_weight_bias(self, weight, bias):
        # save to file
        np.save(weight, self.awss3.tmp + self.weight_file)
        np.save(bias, self.awss3.tmp + self.bias_file)

    @abstractmethod
    def get_params(self):
        pass

    @abstractmethod
    def fit(self):
        pass

    @abstractmethod
    def evaluate(self):
        pass

    @abstractmethod
    def data_preprocessing(self):
        """

        Returns
        -------

        """

    @abstractmethod
    def create_model(self):
        """
        """
