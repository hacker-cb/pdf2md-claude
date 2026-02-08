<!-- PDF_PAGE_BEGIN 1 -->

# Definition of commands

## 11.1 General

Unused opcodes are reserved for future needs.

## 11.2 Overview sheets

Table 17 gives an overview of the standard commands. The special commands overview can be found in Table 18.

**Table 17 – Standard commands**

<table>
<thead>
<tr>
<th rowspan="3">Command name</th>
<th colspan="2">Address byte</th>
<th rowspan="3">Opcode<br>byte</th>
<th rowspan="3">Ed. 1 command<br>number</th>
<th rowspan="3">DTR0</th>
<th rowspan="3">DTR1</th>
<th rowspan="3">DTR2</th>
<th rowspan="3">Answer</th>
<th rowspan="3">Send twice</th>
<th rowspan="3">References</th>
<th rowspan="3">Command<br>reference</th>
</tr>
<tr>
<th rowspan="2">See 7.2.2</th>
<th rowspan="2">Select<br>or bit</th>
</tr>
<tr>
</tr>
</thead>
<tbody>
<tr>
<td>DAPC (<em>level</em>)</td>
<td><em>Device</em></td>
<td>0</td>
<td><em>level</em></td>
<td>-</td>
<td></td>
<td></td>
<td></td>
<td></td>
<td></td>
<td>9.4, 9.7.3, 9.8</td>
<td>11.3.1</td>
</tr>
<tr>
<td></td>
<td></td>
<td></td>
<td></td>
<td></td>
<td></td>
<td></td>
<td></td>
<td></td>
<td></td>
<td></td>
<td></td>
</tr>
<tr>
<td>OFF</td>
<td><em>Device</em></td>
<td>1</td>
<td>0x00</td>
<td>0</td>
<td></td>
<td></td>
<td></td>
<td></td>
<td></td>
<td>9.7.2</td>
<td>11.3.2</td>
</tr>
<tr>
<td>UP</td>
<td><em>Device</em></td>
<td>1</td>
<td>0x01</td>
<td>1</td>
<td></td>
<td></td>
<td></td>
<td></td>
<td></td>
<td>9.7.3</td>
<td>11.3.3</td>
</tr>
<tr>
<td>DOWN</td>
<td><em>Device</em></td>
<td>1</td>
<td>0x02</td>
<td>2</td>
<td></td>
<td></td>
<td></td>
<td></td>
<td></td>
<td>9.7.3</td>
<td>11.3.4</td>
</tr>
<tr>
<td>STEP UP</td>
<td><em>Device</em></td>
<td>1</td>
<td>0x03</td>
<td>3</td>
<td></td>
<td></td>
<td></td>
<td></td>
<td></td>
<td>9.7.2</td>
<td>11.3.5</td>
</tr>
<tr>
<td>STEP DOWN</td>
<td><em>Device</em></td>
<td>1</td>
<td>0x04</td>
<td>4</td>
<td></td>
<td></td>
<td></td>
<td></td>
<td></td>
<td>9.7.2</td>
<td>11.3.6</td>
</tr>
<tr>
<td>RECALL MAX LEVEL</td>
<td><em>Device</em></td>
<td>1</td>
<td>0x05</td>
<td>5</td>
<td></td>
<td></td>
<td></td>
<td></td>
<td></td>
<td>9.7.2, 9.14.3</td>
<td>11.3.7</td>
</tr>
<tr>
<td>RECALL MIN LEVEL</td>
<td><em>Device</em></td>
<td>1</td>
<td>0x06</td>
<td>6</td>
<td></td>
<td></td>
<td></td>
<td></td>
<td></td>
<td>9.7.2, 9.14.3</td>
<td>11.3.8</td>
</tr>
<tr>
<td>STEP DOWN AND OFF</td>
<td><em>Device</em></td>
<td>1</td>
<td>0x07</td>
<td>7</td>
<td></td>
<td></td>
<td></td>
<td></td>
<td></td>
<td>9.7.2</td>
<td>11.3.9</td>
</tr>
<tr>
<td>ON AND STEP UP</td>
<td><em>Device</em></td>
<td>1</td>
<td>0x08</td>
<td>8</td>
<td></td>
<td></td>
<td></td>
<td></td>
<td></td>
<td>9.7.2</td>
<td>11.3.10</td>
</tr>
<tr>
<td>ENABLE DAPC SEQUENCE</td>
<td><em>Device</em></td>
<td>1</td>
<td>0x09</td>
<td>9</td>
<td></td>
<td></td>
<td></td>
<td></td>
<td></td>
<td>9.8</td>
<td>11.3.11</td>
</tr>
<tr>
<td>GO TO LAST ACTIVE LEVEL</td>
<td><em>Device</em></td>
<td>1</td>
<td>0x0A</td>
<td></td>
<td></td>
<td></td>
<td></td>
<td></td>
<td></td>
<td>9.7.3</td>
<td>11.3.12</td>
</tr>
<tr>
<td>CONTINUOUS UP</td>
<td><em>Device</em></td>
<td>1</td>
<td>0x0B</td>
<td></td>
<td></td>
<td></td>
<td></td>
<td></td>
<td></td>
<td>9.7.3</td>
<td>11.3.13</td>
</tr>

<!-- PDF_PAGE_END 1 -->

<!-- PDF_PAGE_BEGIN 2 -->

<tr>
<td>CONTINUOUS DOWN</td>
<td><em>Device</em></td>
<td>1</td>
<td>0x0C</td>
<td></td>
<td></td>
<td></td>
<td></td>
<td></td>
<td></td>
<td>9.7.3</td>
<td>11.3.14</td>
</tr>
<tr>
<td>GO TO SCENE (<em>sceneNumber</em>)<sup>a</sup></td>
<td><em>Device</em></td>
<td>1</td>
<td>0x10 + <em>sceneNumber</em></td>
<td>16 to 31</td>
<td></td>
<td></td>
<td></td>
<td></td>
<td></td>
<td>9.7.3, 9.19</td>
<td>11.3.15</td>
</tr>
<tr>
<td></td>
<td></td>
<td></td>
<td></td>
<td></td>
<td></td>
<td></td>
<td></td>
<td></td>
<td></td>
<td></td>
<td></td>
</tr>
<tr>
<td>RESET</td>
<td><em>Device</em></td>
<td>1</td>
<td>0x20</td>
<td>32</td>
<td></td>
<td></td>
<td></td>
<td></td>
<td>✓</td>
<td>9.11.1, 10</td>
<td>11.4.2</td>
</tr>
<tr>
<td>STORE ACTUAL LEVEL IN DTR0</td>
<td><em>Device</em></td>
<td>1</td>
<td>0x21</td>
<td>33</td>
<td>✓</td>
<td></td>
<td></td>
<td></td>
<td>✓</td>
<td></td>
<td>11.4.3</td>
</tr>
<tr>
<td><em>Reserved</em><sup>b</sup></td>
<td><em>Device</em></td>
<td>1</td>
<td>0x22 <sup>b</sup></td>
<td></td>
<td></td>
<td></td>
<td></td>
<td></td>
<td>✓</td>
<td></td>
<td></td>
</tr>
<tr>
<td>SET OPERATING MODE (<em>DTR0</em>)</td>
<td><em>Device</em></td>
<td>1</td>
<td>0x23</td>
<td></td>
<td>✓</td>
<td></td>
<td></td>
<td></td>
<td>✓</td>
<td>9.9</td>
<td>11.4.4</td>
</tr>
<tr>
<td>RESET MEMORY BANK (<em>DTR0</em>)</td>
<td><em>Device</em></td>
<td>1</td>
<td>0x24</td>
<td></td>
<td>✓</td>
<td></td>
<td></td>
<td></td>
<td>✓</td>
<td>9.11.2</td>
<td>11.4.5</td>
</tr>
<tr>
<td>IDENTIFY DEVICE</td>
<td><em>Device</em></td>
<td>1</td>
<td>0x25</td>
<td></td>
<td></td>
<td></td>
<td></td>
<td></td>
<td>✓</td>
<td>9.14.3</td>
<td>11.4.6</td>
</tr>
<tr>
<td></td>
<td></td>
<td></td>
<td></td>
<td></td>
<td></td>
<td></td>
<td></td>
<td></td>
<td></td>
<td></td>
<td></td>
</tr>
<tr>
<td>SET MAX LEVEL (<em>DTR0</em>)</td>
<td><em>Device</em></td>
<td>1</td>
<td>0x2A</td>
<td>42</td>
<td>✓</td>
<td></td>
<td></td>
<td></td>
<td>✓</td>
<td>9.6</td>
<td>11.4.7</td>
</tr>
<tr>
<td>SET MIN LEVEL (<em>DTR0</em>)</td>
<td><em>Device</em></td>
<td>1</td>
<td>0x2B</td>
<td>43</td>
<td>✓</td>
<td></td>
<td></td>
<td></td>
<td>✓</td>
<td>9.6</td>
<td>11.4.8</td>
</tr>
<tr>
<td>SET SYSTEM FAILURE LEVEL (<em>DTR0</em>)</td>
<td><em>Device</em></td>
<td>1</td>
<td>0x2C</td>
<td>44</td>
<td>✓</td>
<td></td>
<td></td>
<td></td>
<td>✓</td>
<td>9.12</td>
<td>11.4.9</td>
</tr>
<tr>
<td>SET POWER ON LEVEL (<em>DTR0</em>)</td>
<td><em>Device</em></td>
<td>1</td>
<td>0x2D</td>
<td>45</td>
<td>✓</td>
<td></td>
<td></td>
<td></td>
<td>✓</td>
<td>9.13</td>
<td>11.4.10</td>
</tr>
<tr>
<td>SET FADE TIME (<em>DTR0</em>)</td>
<td><em>Device</em></td>
<td>1</td>
<td>0x2E</td>
<td>46</td>
<td>✓</td>
<td></td>
<td></td>
<td></td>
<td>✓</td>
<td>9.5.2</td>
<td>11.4.11</td>
</tr>
<tr>
<td>SET FADE RATE (<em>DTR0</em>)</td>
<td><em>Device</em></td>
<td>1</td>
<td>0x2F</td>
<td>47</td>
<td>✓</td>
<td></td>
<td></td>
<td></td>
<td>✓</td>
<td>9.5.3</td>
<td>11.4.12</td>
</tr>
<tr>
<td>SET EXTENDED FADE TIME (<em>DTR0</em>)</td>
<td><em>Device</em></td>
<td>1</td>
<td>0x30</td>
<td></td>
<td>✓</td>
<td></td>
<td></td>
<td></td>
<td>✓</td>
<td>9.5.4</td>
<td>11.4.13</td>
</tr>
<tr>
<td>SET SCENE (<em>DTR0</em>, <em>sceneX</em>)<sup>a</sup></td>
<td><em>Device</em></td>
<td>1</td>
<td>0x40 + <em>sceneNumber</em></td>
<td>64 to 79</td>
<td>✓</td>
<td></td>
<td></td>
<td></td>
<td>✓</td>
<td>9.19</td>
<td>11.4.14</td>
</tr>
<tr>
<td></td>
<td></td>
<td></td>
<td></td>
<td></td>
<td></td>
<td></td>
<td></td>
<td></td>
<td></td>
<td></td>
<td></td>
</tr>
<tr>
<td>REMOVE FROM SCENE (<em>sceneX</em>)<sup>a</sup></td>
<td><em>Device</em></td>
<td>1</td>
<td>0x50 + <em>sceneNumber</em></td>
<td>80 to 95</td>
<td></td>
<td></td>
<td></td>
<td></td>
<td>✓</td>
<td>9.19</td>
<td>11.4.15</td>
</tr>
<tr>
<td>ADD TO GROUP (<em>group</em>)<sup>a</sup></td>
<td><em>Device</em></td>
<td>1</td>
<td>0x60 + <em>group</em></td>
<td>96 to 111</td>
<td></td>
<td></td>
<td></td>
<td></td>
<td>✓</td>
<td></td>
<td>11.4.16</td>
</tr>
<tr>
<td>REMOVE FROM GROUP (<em>group</em>)<sup>a</sup></td>
<td><em>Device</em></td>
<td>1</td>
<td>0x70 + <em>group</em></td>
<td>112 to 127</td>
<td></td>
<td></td>
<td></td>
<td></td>
<td>✓</td>
<td></td>
<td>11.4.17</td>
</tr>
<tr>
<td>SET SHORT ADDRESS (<em>DTR0</em>)</td>
<td><em>Device</em></td>
<td>1</td>
<td>0x80</td>
<td>128</td>
<td>✓</td>
<td></td>
<td></td>
<td></td>
<td>✓</td>
<td>9.14.4</td>
<td>11.4.18</td>
</tr>
<tr>
<td>ENABLE WRITE MEMORY</td>
<td><em>Device</em></td>
<td>1</td>
<td>0x81</td>
<td>129</td>
<td></td>
<td></td>
<td></td>
<td></td>
<td>✓</td>
<td>9.10.6</td>
<td>11.4.19</td>
</tr>

<!-- PDF_PAGE_END 2 -->

<!-- PDF_PAGE_BEGIN 3 -->

<tr>
<td><em>Reserved for IEC 62386-104 (see [3])</em></td>
<td><em>Device</em></td>
<td>1</td>
<td>0x82</td>
<td></td>
<td>✓</td>
<td></td>
<td></td>
<td></td>
<td></td>
<td></td>
<td></td>
</tr>
<tr>
<td></td>
<td></td>
<td></td>
<td></td>
<td></td>
<td></td>
<td></td>
<td></td>
<td></td>
<td></td>
<td></td>
<td></td>
</tr>
<tr>
<td>QUERY STATUS</td>
<td><em>Device</em></td>
<td>1</td>
<td>0x90</td>
<td>144</td>
<td></td>
<td></td>
<td></td>
<td>✓</td>
<td></td>
<td>9.16</td>
<td>11.5.2</td>
</tr>
<tr>
<td>QUERY CONTROL GEAR PRESENT</td>
<td><em>Device</em></td>
<td>1</td>
<td>0x91</td>
<td>145</td>
<td></td>
<td></td>
<td></td>
<td>✓</td>
<td></td>
<td></td>
<td>11.5.3</td>
</tr>
<tr>
<td>QUERY LAMP FAILURE</td>
<td><em>Device</em></td>
<td>1</td>
<td>0x92</td>
<td>146</td>
<td></td>
<td></td>
<td></td>
<td>✓</td>
<td></td>
<td></td>
<td>11.5.4</td>
</tr>
<tr>
<td>QUERY LAMP POWER ON</td>
<td><em>Device</em></td>
<td>1</td>
<td>0x93</td>
<td>147</td>
<td></td>
<td></td>
<td></td>
<td>✓</td>
<td></td>
<td></td>
<td>11.5.6</td>
</tr>
<tr>
<td>QUERY LIMIT ERROR</td>
<td><em>Device</em></td>
<td>1</td>
<td>0x94</td>
<td>148</td>
<td></td>
<td></td>
<td></td>
<td>✓</td>
<td></td>
<td></td>
<td>11.5.7</td>
</tr>
<tr>
<td>QUERY RESET STATE</td>
<td><em>Device</em></td>
<td>1</td>
<td>0x95</td>
<td>149</td>
<td></td>
<td></td>
<td></td>
<td>✓</td>
<td></td>
<td></td>
<td>11.5.8</td>
</tr>
<tr>
<td>QUERY MISSING SHORT ADDRESS</td>
<td><em>Device</em></td>
<td>1</td>
<td>0x96</td>
<td>150</td>
<td></td>
<td></td>
<td></td>
<td>✓</td>
<td></td>
<td>9.14.2</td>
<td>11.5.9</td>
</tr>
<tr>
<td>QUERY VERSION NUMBER</td>
<td><em>Device</em></td>
<td>1</td>
<td>0x97</td>
<td>151</td>
<td></td>
<td></td>
<td></td>
<td>✓</td>
<td></td>
<td></td>
<td>11.5.10</td>
</tr>
<tr>
<td>QUERY CONTENT DTR0</td>
<td><em>Device</em></td>
<td>1</td>
<td>0x98</td>
<td>152</td>
<td>✓</td>
<td></td>
<td></td>
<td>✓</td>
<td></td>
<td></td>
<td>11.5.11</td>
</tr>
<tr>
<td>QUERY DEVICE TYPE</td>
<td><em>Device</em></td>
<td>1</td>
<td>0x99</td>
<td>153</td>
<td></td>
<td></td>
<td></td>
<td>✓</td>
<td></td>
<td>9.18</td>
<td>11.5.12</td>
</tr>
<tr>
<td>QUERY PHYSICAL MINIMUM</td>
<td><em>Device</em></td>
<td>1</td>
<td>0x9A</td>
<td>154</td>
<td></td>
<td></td>
<td></td>
<td>✓</td>
<td></td>
<td></td>
<td>11.5.13</td>
</tr>
<tr>
<td>QUERY POWER FAILURE</td>
<td><em>Device</em></td>
<td>1</td>
<td>0x9B</td>
<td>155</td>
<td></td>
<td></td>
<td></td>
<td>✓</td>
<td></td>
<td></td>
<td>11.5.15</td>
</tr>
<tr>
<td>QUERY CONTENT DTR1</td>
<td><em>Device</em></td>
<td>1</td>
<td>0x9C</td>
<td>156</td>
<td></td>
<td>✓</td>
<td></td>
<td>✓</td>
<td></td>
<td></td>
<td>11.5.16</td>
</tr>
<tr>
<td>QUERY CONTENT DTR2</td>
<td><em>Device</em></td>
<td>1</td>
<td>0x9D</td>
<td>157</td>
<td></td>
<td></td>
<td>✓</td>
<td>✓</td>
<td></td>
<td></td>
<td>11.5.17</td>
</tr>
<tr>
<td>QUERY OPERATING MODE</td>
<td><em>Device</em></td>
<td>1</td>
<td>0x9E</td>
<td></td>
<td></td>
<td></td>
<td></td>
<td>✓</td>
<td></td>
<td>9.9</td>
<td>11.5.18</td>
</tr>
<tr>
<td>QUERY LIGHT SOURCE TYPE</td>
<td><em>Device</em></td>
<td>1</td>
<td>0x9F</td>
<td></td>
<td>✓</td>
<td>✓</td>
<td>✓</td>
<td>✓</td>
<td></td>
<td></td>
<td>11.5.19</td>
</tr>
<tr>
<td></td>
<td></td>
<td></td>
<td></td>
<td></td>
<td></td>
<td></td>
<td></td>
<td></td>
<td></td>
<td></td>
<td></td>
</tr>
<tr>
<td>QUERY ACTUAL LEVEL</td>
<td><em>Device</em></td>
<td>1</td>
<td>0xA0</td>
<td>160</td>
<td></td>
<td></td>
<td></td>
<td>✓</td>
<td></td>
<td></td>
<td>11.5.20</td>
</tr>
<tr>
<td>QUERY MAX LEVEL</td>
<td><em>Device</em></td>
<td>1</td>
<td>0xA1</td>
<td>161</td>
<td></td>
<td></td>
<td></td>
<td>✓</td>
<td></td>
<td></td>
<td>11.5.21</td>
</tr>
<tr>
<td>QUERY MIN LEVEL</td>
<td><em>Device</em></td>
<td>1</td>
<td>0xA2</td>
<td>162</td>
<td></td>
<td></td>
<td></td>
<td>✓</td>
<td></td>
<td></td>
<td>11.5.22</td>
</tr>
<tr>
<td>QUERY POWER ON LEVEL</td>
<td><em>Device</em></td>
<td>1</td>
<td>0xA3</td>
<td>163</td>
<td></td>
<td></td>
<td></td>
<td>✓</td>
<td></td>
<td>9.13</td>
<td>11.5.23</td>
</tr>
<tr>
<td>QUERY SYSTEM FAILURE LEVEL</td>
<td><em>Device</em></td>
<td>1</td>
<td>0xA4</td>
<td>164</td>
<td></td>
<td></td>
<td></td>
<td>✓</td>
<td></td>
<td>9.12</td>
<td>11.5.24</td>
</tr>

<!-- PDF_PAGE_END 3 -->

<!-- PDF_PAGE_BEGIN 4 -->

<tr>
<td>QUERY FADE TIME/FADE RATE</td>
<td><em>Device</em></td>
<td>1</td>
<td>0xA5</td>
<td>165</td>
<td></td>
<td></td>
<td></td>
<td>✓</td>
<td></td>
<td></td>
<td>11.5.25</td>
</tr>
<tr>
<td>QUERY MANUFACTURER SPECIFIC MODE</td>
<td><em>Device</em></td>
<td>1</td>
<td>0xA6</td>
<td></td>
<td></td>
<td></td>
<td></td>
<td>✓</td>
<td></td>
<td>9.9</td>
<td>11.5.27</td>
</tr>
<tr>
<td>QUERY NEXT DEVICE TYPE</td>
<td><em>Device</em></td>
<td>1</td>
<td>0xA7</td>
<td></td>
<td></td>
<td></td>
<td></td>
<td>✓</td>
<td></td>
<td>9.18</td>
<td>11.5.13</td>
</tr>
<tr>
<td>QUERY EXTENDED FADE TIME</td>
<td><em>Device</em></td>
<td>1</td>
<td>0xA8</td>
<td></td>
<td></td>
<td></td>
<td></td>
<td>✓</td>
<td></td>
<td>9.5.4</td>
<td>11.5.26</td>
</tr>
<tr>
<td>QUERY CONTROL GEAR FAILURE</td>
<td><em>Device</em></td>
<td>1</td>
<td>0xAA</td>
<td></td>
<td></td>
<td></td>
<td></td>
<td>✓</td>
<td></td>
<td>9.16.2</td>
<td>11.5.4</td>
</tr>
<tr>
<td><em>Reserved for IEC 62386-104 (see [3])</em></td>
<td><em>Device</em></td>
<td>1</td>
<td>0xAB</td>
<td></td>
<td></td>
<td></td>
<td></td>
<td>✓</td>
<td></td>
<td></td>
<td></td>
</tr>
<tr>
<td></td>
<td></td>
<td></td>
<td></td>
<td></td>
<td></td>
<td></td>
<td></td>
<td></td>
<td></td>
<td></td>
<td></td>
</tr>
<tr>
<td>QUERY SCENE LEVEL (<em>sceneX</em>)<sup>a</sup></td>
<td><em>Device</em></td>
<td>1</td>
<td>0xB0 + <em>sceneNumber</em></td>
<td>176 to 191</td>
<td></td>
<td></td>
<td></td>
<td>✓</td>
<td></td>
<td>9.19</td>
<td>11.5.28</td>
</tr>
<tr>
<td>QUERY GROUPS 0-7</td>
<td><em>Device</em></td>
<td>1</td>
<td>0xC0</td>
<td>192</td>
<td></td>
<td></td>
<td></td>
<td>✓</td>
<td></td>
<td></td>
<td>11.5.29</td>
</tr>
<tr>
<td>QUERY GROUPS 8-15</td>
<td><em>Device</em></td>
<td>1</td>
<td>0xC1</td>
<td>193</td>
<td></td>
<td></td>
<td></td>
<td>✓</td>
<td></td>
<td></td>
<td>11.5.30</td>
</tr>
<tr>
<td>QUERY RANDOM ADDRESS (H)</td>
<td><em>Device</em></td>
<td>1</td>
<td>0xC2</td>
<td>194</td>
<td></td>
<td></td>
<td></td>
<td>✓</td>
<td></td>
<td></td>
<td>11.5.31</td>
</tr>
<tr>
<td>QUERY RANDOM ADDRESS (M)</td>
<td><em>Device</em></td>
<td>1</td>
<td>0xC3</td>
<td>195</td>
<td></td>
<td></td>
<td></td>
<td>✓</td>
<td></td>
<td></td>
<td>11.5.32</td>
</tr>
<tr>
<td>QUERY RANDOM ADDRESS (L)</td>
<td><em>Device</em></td>
<td>1</td>
<td>0xC4</td>
<td>196</td>
<td></td>
<td></td>
<td></td>
<td>✓</td>
<td></td>
<td></td>
<td>11.5.33</td>
</tr>
<tr>
<td>READ MEMORY LOCATION (<em>DTR1, DTR0</em>)</td>
<td><em>Device</em></td>
<td>1</td>
<td>0xC5</td>
<td>197</td>
<td>✓</td>
<td>✓</td>
<td></td>
<td>✓</td>
<td></td>
<td>9.10.5</td>
<td>11.5.34</td>
</tr>
<tr>
<td>Application extended commands</td>
<td><em>Device</em></td>
<td>1</td>
<td>0xE0 to 0xFE</td>
<td>224 to 254</td>
<td>?</td>
<td>?</td>
<td>?</td>
<td>?</td>
<td>?</td>
<td>9.18</td>
<td>11.6</td>
</tr>
<tr>
<td></td>
<td></td>
<td></td>
<td></td>
<td></td>
<td></td>
<td></td>
<td></td>
<td></td>
<td></td>
<td></td>
<td></td>
</tr>
<tr>
<td>QUERY EXTENDED VERSION NUMBER</td>
<td><em>Device</em></td>
<td>1</td>
<td>0xFF</td>
<td>255</td>
<td></td>
<td></td>
<td></td>
<td>✓</td>
<td></td>
<td></td>
<td>11.6.2</td>
</tr>
</tbody>
</table>

<sup>a</sup> There is one command per scene, so there are actually 16 commands for scenes 0 to 15. Similarly for the 16 group commands.

<sup>b</sup> Reserved to maintain backward compatibility due to use in Edition 2 of IEC 62386-102:2014 (see [2]).

<!-- PDF_PAGE_END 4 -->