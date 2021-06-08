import gym
import json
import datetime as dt
from stable_baselines.common.policies import MlpPolicy
from stable_baselines.common.vec_env import DummyVecEnv
from stable_baselines import PPO2
from env.BitcoinTradingEnv import BitcoinTradingEnv
import pandas as pd
from tensorflow.python.compiler.tensorrt import trt_convert as trt

class BitcoinTradingEnv(gym.Env):
    """A Bitcoin trading environment for OpenAI gym"""
    metadata = {'render.modes': ['live', 'file', 'none']}
    scaler = preprocessing.MinMaxScaler()
    viewer = None
    
    MAX_TRADING_SESSION = 100000  # ~2 months
    
    def __init__(self, df, lookback_window_size=50, commission=0.00075,  initial_balance=10000, serial=False):
        super(BitcoinTradingEnv, self).__init__()
        self.df = df.dropna().reset_index()
        self.lookback_window_size = lookback_window_size
        self.initial_balance = initial_balance
        self.commission = commission
        self.serial = serial
        # Actions of the format Buy 1/10, Sell 3/10, Hold, etc.
        self.action_space = spaces.MultiDiscrete([3, 10])
        # Observes the OHCLV values, net worth, and trade history
        self.observation_space = spaces.Box(low=0, high=1, shape=(10, 
                    lookback_window_size + 1), dtype=np.float16)
        
    def reset(self):
        self.balance = self.initial_balance
        self.net_worth = self.initial_balance
        self.btc_held = 0
        self._reset_session()
        self.account_history = np.repeat([
        [self.net_worth],
        [0],
        [0],
        [0],
        [0]
        ], self.lookback_window_size + 1, axis=1)
        self.trades = []
        return self._next_observation()
    
    def _reset_session(self):
        self.current_step = 0
        if self.serial:
            self.steps_left = len(self.df) - self.lookback_window_size - 1
            self.frame_start = self.lookback_window_size
        else:
            self.steps_left = np.random.randint(1, MAX_TRADING_SESSION)
            self.frame_start = np.random.randint(self.lookback_window_size, len(self.df) - self.steps_left)
            self.active_df = self.df[self.frame_start -   self.lookback_window_size:self.frame_start + self.steps_left]
    
    def _next_observation(self):
        end = self.current_step + self.lookback_window_size + 1
        obs = np.array([
            self.active_df['Open'].values[self.current_step:end],  
            self.active_df['High'].values[self.current_step:end],
            self.active_df['Low'].values[self.current_step:end],
            self.active_df['Close'].values[self.current_step:end],
            self.active_df['Volume_(BTC)'].values[self.current_step:end],
        ])
        scaled_history = self.scaler.fit_transform(self.account_history)
        obs = np.append(obs, scaled_history[:, -(self.lookback_window_size + 1):], axis=0)
        return obs
    
    def step(self, action):
        current_price = self._get_current_price() + 0.01
        self._take_action(action, current_price)
        self.steps_left -= 1
        self.current_step += 1
        if self.steps_left == 0:
            self.balance += self.btc_held * current_price
            self.btc_held = 0
            self._reset_session()
        obs = self._next_observation()
        reward = self.net_worth
        done = self.net_worth <= 0
        return obs, reward, done, {}
    
    def _take_action(self, action, current_price):
        action_type = action[0]
        amount = action[1] / 10
        btc_bought = 0
        btc_sold = 0
        cost = 0
        sales = 0
        if action_type < 1:
            btc_bought = self.balance / current_price * amount
            cost = btc_bought * current_price * (1 + self.commission)
            self.btc_held += btc_bought
            self.balance -= cost
        elif action_type < 2:
            btc_sold = self.btc_held * amount
            sales = btc_sold * current_price  * (1 - self.commission)
            self.btc_held -= btc_sold
            self.balance += sales

        if btc_sold > 0 or btc_bought > 0:
            self.trades.append({
              'step': self.frame_start+self.current_step,
              'amount': btc_sold if btc_sold > 0 else btc_bought,
              'total': sales if btc_sold > 0 else cost,
              'type': "sell" if btc_sold > 0 else "buy"
            })
        self.net_worth = self.balance + self.btc_held * current_price
        self.account_history = np.append(self.account_history, [
            [self.net_worth],
            [btc_bought],
            [cost],
            [btc_sold],
            [sales]
            ], axis=1)
        
    def render(self, mode='human', **kwargs):
        if mode == 'human':
            if self.viewer == None:
                self.viewer = BitcoinTradingGraph(self.df,kwargs.get('title', None))
            self.viewer.render(self.frame_start + self.current_step,
                           self.net_worth,
                           self.trades,
                           window_size=self.lookback_window_size)