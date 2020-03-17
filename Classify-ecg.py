from wfdb import io, plot
import wfdb
import os
import gc
import numpy as np
import matplotlib.pyplot as plt
from tqdm import tqdm_notebook as tqdm
import pandas as pd
import math
from keras.models import Sequential
from keras.layers import Dense
from keras.layers import Dropout, Input
from keras.layers import CuDNNLSTM, LSTM
from keras.callbacks import ModelCheckpoint
from sklearn.utils import shuffle
from sklearn.metrics import confusion_matrix
import time
import keras


def comments_to_dict(comments):
    key_value_pairs = [comment.split(':') for comment in comments]
    return {pair[0]: pair[1] for pair in key_value_pairs}    

data_folder = 'data'
db = 'ptbdb'
record_names = io.get_record_list(db)
record_names

def record_to_row(record, patient_id):
    row = {}
    row['patient'] = patient_id
    row['name'] = record.record_name
    row['label'] = comments_to_dict(record.comments)['Reason for admission'][1:]
    row['signals'] = record.p_signal
    row['signal_length'] = record.sig_len
    channels = record.sig_name
    signals = record.p_signal.transpose()
    
    row['channels'] = channels
    
    for channel, signal in zip(channels, signals):
        row[channel] = signal
        
    return row


records = []
for record_name in tqdm(record_names):
    record = io.rdrecord(record_name=os.path.join('data', record_name))
    label = comments_to_dict(record.comments)['Reason for admission'][1:]
    patient = record_name.split('/')[0]
    signal_length = record.sig_len
    records.append({'name':record_name, 'label':label, 'patient':patient, 'signal_length':signal_length})
    
channels = record.sig_name
df_records = pd.DataFrame(records)    


labels = df_records['label'].unique()
df_records['label'].value_counts()


selected_labels = [
    'Healthy control',
    'Myocardial infarction'
    ]
df_selected = df_records.loc[df_records['label'].isin(selected_labels)]
label_map = {label: value for label, value in zip(selected_labels, range(len(selected_labels)))}


test_patients = []
train_patients = []
test_size = 0.2
channels
for label in selected_labels:
    df_selected = df_records.loc[df_records['label'] == label]
    patients = df_selected['patient'].unique()
    n_test = math.ceil(len(patients)*test_size)
    test_patients+=list(np.random.choice(patients, n_test, replace=False))
    train_patients+=list(patients[np.isin(patients, test_patients, invert=True)])
    

def make_set(df_data, channels, label_map, record_id, window_size=2048):
    n_windows = 0
    
    for _, record in tqdm(df_data.iterrows()):
        n_windows+= record['signal_length']//window_size

    dataX = np.zeros((n_windows, len(channels), window_size))
    dataY = np.zeros((n_windows, len(label_map)))
    
    record_list = []
    
    nth_window = 0
    for i, (patient, record) in enumerate(tqdm(df_data.iterrows())):
        # read the record, get the signal data and transpose it
        signal_data = io.rdrecord(os.path.join('data', record['name'])).p_signal.transpose()
        n_rows = signal_data.shape[-1]
        n_windows = n_rows//window_size
        dataX[nth_window:nth_window+n_windows] = np.array([signal_data[:,i*window_size:(i+1)*window_size] for i in range(n_windows)])
        dataY[nth_window:nth_window+n_windows][:, label_map[record.label]] = 1
        nth_window+=n_windows
        
        if record_id:
            record_list+= n_windows*[record['name']]
        
    return dataX, dataY, record_list


df_patient_records = df_records.set_index('patient')
df_train_patients = df_patient_records.loc[train_patients]
df_test_patients = df_patient_records.loc[test_patients]
window_size = 2048#df_records['signal_length'].min()
#trainX, trainY, _ = make_set(df_train_patients, channels, label_map, False, window_size)
testX, testY, record_list = make_set(df_test_patients, channels, label_map, True, window_size)


# In[11]:


def make_model(input_shape, output_dim, lstm_layer, dropout=0.2):
    print("model dim: ", input_shape, output_dim)
    model = Sequential()
    model.add(lstm_layer(256, return_sequences=True, input_shape=input_shape, batch_size=None))
    model.add(Dropout(dropout))
    model.add(lstm_layer(128, return_sequences=True))
    model.add(Dropout(dropout))
    model.add(LSTM(64))
    model.add(Dropout(dropout))
    model.add(Dense(output_dim, activation='softmax'))
    model.compile(loss='categorical_crossentropy', optimizer='adam')
    
    return model


# In[12]:


class TimeHistory(keras.callbacks.Callback):
    def on_train_begin(self, logs={}):
        self.times = []

    def on_epoch_begin(self, batch, logs={}):
        self.epoch_time_start = time.time()

    def on_epoch_end(self, batch, logs={}):
        self.times.append(time.time() - self.epoch_time_start)


# In[13]:


np.random.seed(1337)
test_patients = []
train_patients = []
test_size = 0.2
channels
for label in selected_labels:
    df_selected = df_records.loc[df_records['label'] == label]
    patients = df_selected['patient'].unique()
    n_test = math.ceil(len(patients)*test_size)
    test_patients+=list(np.random.choice(patients, n_test, replace=False))
    train_patients+=list(patients[np.isin(patients, test_patients, invert=True)])
    
df_patient_records = df_records.set_index('patient')
df_train_patients = df_patient_records.loc[train_patients]
df_test_patients = df_patient_records.loc[test_patients]
window_size = 2048#df_records['signal_length'].min()
trainX, trainY, _ = make_set(df_train_patients, channels, label_map, False, window_size)
testX, testY, record_list = make_set(df_test_patients, channels, label_map, True, window_size)

#Shuffle order of train set
trainX, trainY = shuffle(trainX, trainY)

#Since we have a large class inbalance we need to udjust the weights for it.
fractions = 1-trainY.sum(axis=0)/len(trainY)
weights = fractions[trainY.argmax(axis=1)]

#df_selected['patient'].sample(len())

filepath = os.path.join('models', "weights-improvement-{epoch:02d}-bigger.hdf5")
checkpoint = ModelCheckpoint(filepath, monitor='loss', verbose=1, save_best_only=True, mode='min')

model_name = 'two_classes'
model_folder = os.path.join('tensorlogs', model_name + "-logs/")

if not os.path.isdir(model_folder):
    n_logs = 0
else:
    n_logs = len(os.listdir(model_folder))
    
tensorboard_logs = os.path.join(model_folder, "%inth_run"%n_logs)
tensorboard_callback = keras.callbacks.TensorBoard(log_dir=tensorboard_logs, write_graph=False)
time_callback = TimeHistory()
callbacks = [checkpoint, tensorboard_callback, time_callback]

model = make_model((trainX.shape[1], trainX.shape[2]), trainY.shape[-1], CuDNNLSTM)

model.fit(trainX, trainY, epochs=50, batch_size=512, sample_weight=weights, callbacks=callbacks)


# In[ ]:


output = model.predict_classes(testX)


# In[ ]:


confusion_matrix(testY.argmax(axis=1), output)


# In[ ]:


(output == testY.argmax(axis=1)).sum()/len(output)


# In[ ]:


summed = pd.DataFrame({'record':record_list, 'predictions':output, 'label':testY.argmax(axis=1)}).groupby('record').mean()
control = summed.loc[summed['label'] == 0]
print("Control accuracy: "+  str((control['predictions'] <= 0.5).sum()/control.shape[0]))

infarct = summed.loc[summed['label'] == 1]
print("Infarct accuracy: "+  str((infarct['predictions'] > 0.5).sum()/infarct.shape[0]))


# In[ ]:


control.hist()


# In[ ]:


channel = channels[1]
plt.plot(df_healthy.iloc[3][channel][0:window_size])
plt.plot(df_myo.iloc[4][channel][0:window_size])
plt.plot(df_dys.iloc[2][channel][0:window_size])
plt.show()


# In[ ]:


record.label = comments_to_dict(record.comments)['Reason for admission']


# In[ ]:


record.label


# In[ ]:


comments_to_dict(record.comments)


# In[ ]:


io.dl_database(db, 'data', records)
